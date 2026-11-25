"""Gate G2 Reconciliation's reconciliation checks (docs/diligence.md §5), for `fin.diligence.gates`.

A reconciliation is required for a period when its inputs are loaded (sales and purchase register
vs GL: the period's Ledger Vouchers and registers covering it; gl_vs_tb: its trial balance and
Ledger Vouchers; payroll_vs_gl: an annual period's payroll file and Ledger Vouchers). The stub is
checked like an annual period, except payroll. For each required one the checklist says:

- it ran on the current data (its latest run's input hash equals what is compared now);
- every line at or above the threshold (needs_query) is in a query; fx, not reconciled and
  unmapped lines are listed, not queried.

An engagement with nothing required adds no line. The inputs (each required run, its lines that
need a query and their queries) go into G2's hash, so a new query on a line, or a run on changed
data, reopens it.
"""

from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile import RECONCILIATIONS, periods_of, store
from fin.reconcile.base import input_hash

Line = tuple[bool, str]


def required(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (name, period)
        for period in periods_of(engagement)
        for name, (_, applies) in RECONCILIATIONS.items()
        if applies(con, engagement, period)
    ]


def _current(ctx: Any, name: str, period: str) -> str | None:
    """The hash of what the reconciliation compares now, or the E840 message when it cannot run."""
    compute = RECONCILIATIONS[name][0]
    try:
        computed = compute(ctx.con, ctx.company, ctx.engagement, period)
    except ReconCannotRun as error:
        return f"cannot run: {error}"
    return "hash:" + input_hash(computed, ctx.engagement["query_threshold"])


def _line(ctx: Any, name: str, period: str) -> Line:
    latest = store.latest_run(ctx.con, ctx.engagement["engagement_id"], name, period)
    now = _current(ctx, name, period)
    command = f"`fin reconcile {ctx.company} {name} --period {period}`"
    if now is not None and not now.startswith("hash:"):
        return False, f"{period}: {name} {now}"
    if latest is None:
        return False, f"{period}: {name} has not run ({command})"
    assert now is not None
    if latest[1] != now[5:]:
        return False, f"{period}: {name} ran on data that has changed since ({command})"
    counts = ctx.con.execute(
        "SELECT count(*) FILTER (WHERE kind = 'fx'), "
        "count(*) FILTER (WHERE kind = 'not_reconciled') FROM recon_items WHERE recon_id = ?",
        [latest[0]],
    ).fetchone()
    assert counts is not None
    fx, unreconciled = counts
    if open_lines := store.open_lines(ctx.con, latest[0]):
        return (
            False,
            f"{period}: {name}: {open_lines} lines at or above the threshold are not in a query "
            f"(`fin query raise {ctx.company} --items <ids>`)",
        )
    listed = [
        f"{n} {what} listed, not queried"
        for n, what in ((fx, "fx"), (unreconciled, "not reconciled"))
        if n
    ]
    extra = f" ({'; '.join(listed)})" if listed else ""
    return True, f"{period}: {name} ran; every line at or above the threshold is in a query{extra}"


def checklist(ctx: Any) -> list[Line]:
    """The reconciliation lines of G2's checklist (`ctx`: a `fin.diligence.gates.Context`)."""
    return [_line(ctx, name, period) for name, period in required(ctx.con, ctx.engagement)]


def inputs(ctx: Any) -> dict[str, Any]:
    """G2's reconciliation input: per required run, the current hash and its lines that need a
    query or are in one."""
    runs = []
    for name, period in required(ctx.con, ctx.engagement):
        latest = store.latest_run(ctx.con, ctx.engagement["engagement_id"], name, period)
        items = store.query_rows(ctx.con, None if latest is None else latest[0])
        runs.append([name, period, latest, _current(ctx, name, period), items])
    return {"reconciliations": runs} if runs else {}
