"""The D4 reconciliations (DILIGENCE_PLAN.md D4): one function per reconciliation.

sales_register_vs_gl and purchase_register_vs_gl (`registers.py`), gl_vs_tb (`gl_vs_tb.py`) and
payroll_vs_gl (`payroll_vs_gl.py`) compute lines from the loaded source rows; `store.py` records a
run; `items.py` raises one accountant query from many lines (`fin query raise --items`);
`checks.py` is gate G2's reconciliation checks. tb_vs_statements stays in `fin.diligence.recon`.
Each is deterministic SQL and grouping; none changes company data to make a line tie.
"""

from collections.abc import Callable
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.audit.logger import Run
from fin.db.conn import transaction
from fin.diligence import recon
from fin.diligence.engagement import company_database, require_open
from fin.reconcile import bank, recast, registers, store
from fin.reconcile import gl_vs_tb as gl_module
from fin.reconcile import payroll_vs_gl as payroll_module
from fin.reconcile.base import Computed, input_hash
from fin.reconcile.format import format_result

Compute = Callable[[duckdb.DuckDBPyConnection, str, dict[str, Any], str], Computed]
Applies = Callable[[duckdb.DuckDBPyConnection, dict[str, Any], str], bool]

# name -> (compute, applies): the reconciliations this package runs, in the order they are run
RECONCILIATIONS: dict[str, tuple[Compute, Applies]] = {
    registers.SALES.name: (
        registers.sales_register_vs_gl,
        lambda con, e, p: registers.applies(con, e, p, registers.SALES),
    ),
    registers.PURCHASE.name: (
        registers.purchase_register_vs_gl,
        lambda con, e, p: registers.applies(con, e, p, registers.PURCHASE),
    ),
    gl_module.NAME: (gl_module.gl_vs_tb, gl_module.applies),
    payroll_module.NAME: (payroll_module.payroll_vs_gl, payroll_module.applies),
    bank.INVOICES: (bank.bank_vs_invoices, bank.applies_invoices),
    bank.TB: (bank.bank_vs_tb, bank.applies_tb),
}
NAMES = (recon.NAME, *RECONCILIATIONS)


def current_hash(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], name: str, period: str
) -> str | None:
    """The hash of what the reconciliation compares now (None when it cannot run now)."""
    if name == recon.NAME:
        return recon.input_hash(con, engagement, period)
    if name == recast.NAME:
        return recast.current_hash(con, company, engagement, period)
    try:
        computed = RECONCILIATIONS[name][0](con, company, engagement, period)
    except ReconCannotRun:
        return None
    return input_hash(computed, engagement["query_threshold"])


def periods_of(engagement: dict[str, Any]) -> list[str]:
    """The annual periods of the engagement, then its stub."""
    return [
        *engagement["periods"].split(","),
        *([engagement["stub"]] if engagement.get("stub") else []),
    ]


def run_new(run: Run, company: str, name: str, period: str | None) -> list[dict[str, Any]]:
    """Run one of this package's reconciliations for the period (default: every period it applies
    to), in one transaction. E803 without an open engagement; E840 when it cannot run."""
    compute, applies = RECONCILIATIONS[name]
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        periods = periods_of(engagement)
        if period is not None and period not in periods:
            raise ReconCannotRun(
                f"{period} is not a period of {company}'s open engagement ({', '.join(periods)}); "
                "nothing recorded"
            )
        chosen = [period] if period else [p for p in periods if applies(con, engagement, p)]
        if not chosen:
            raise ReconCannotRun(
                f"{company}: {name} has no period with its inputs loaded (see `fin status "
                f"{company}`); nothing recorded"
            )
        results = [
            store.save(run, con, engagement, name, p, compute(con, company, engagement, p))
            for p in chosen
        ]
    for result in results:
        run.event(
            "reconcile",
            format_result(result).splitlines()[0],
            context={"company": company, "recon_id": result["recon_id"]},
        )
    return results


def run_all(run: Run, company: str, period: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """tb_vs_statements for the annual periods with a trial balance and statement figures, then
    every other reconciliation for the periods it applies to: (results, the E840 messages of the
    ones that could not run, for example no ledger confirmed to revenue yet). E840 when none
    ran."""
    results: list[dict[str, Any]] = []
    not_run: list[str] = []
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        periods = [p for p in periods_of(engagement) if period in (None, p)]
        statements = [
            p
            for p in engagement["periods"].split(",")
            if p in periods
            and recon.trial_balance(con, engagement["engagement_id"], p)
            and recon.statement_figures(con, p)
        ]
        todo = [
            name
            for name, (_, applies) in RECONCILIATIONS.items()
            if any(applies(con, engagement, p) for p in periods)
        ]
    for p in statements:
        results += recon.tb_vs_statements(run, company, p)
    for name in todo:
        try:
            results += run_new(run, company, name, period)
        except ReconCannotRun as error:
            not_run.append(str(error))
    if not results:
        raise ReconCannotRun(
            f"{company}: no reconciliation has its inputs loaded (a trial balance with statements, "
            "Ledger Vouchers with registers, or payroll); nothing recorded"
            + "".join(f"; {message}" for message in not_run)
        )
    return results, not_run
