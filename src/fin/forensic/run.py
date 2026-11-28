"""`fin forensic run`: run the tests for the open engagement's periods and keep every run.

Each (test, period) gets a forensic_runs row and its flags. Earlier runs and their flags stay (a
query keeps the flags it was raised on); the latest is current. A test reports "cannot run" when a
file it reads is not loaded for the period or an input it needs is not set, and "too few entries"
below its minimum population. The input hash is the test's settings, the query threshold, the
loaded Ledger Vouchers, trial balances and registers and the confirmed mappings (a bank test's: the
loaded bank statements and the register parties): when any changes, the run is stale. Each run logs,
per test, the thresholds used, the population, the flagged count and the count just outside a
threshold, with no ledger or party name (owner, 2026-11-28: "tune later but keep logs").
"""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import ForensicTestUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction
from fin.diligence.engagement import company_database, require_open
from fin.diligence.mask import Parties
from fin.forensic import settings as forensic_settings
from fin.forensic.books import Books, Context, load
from fin.forensic.registry import ForensicTest, Outcome, tests
from fin.reconcile import periods_of

KIND_NAMES = {
    "ledger_vouchers": "Ledger Vouchers",
    "trial_balance": "trial balance",
    "bank": "bank statement",
}
UNCONFIRMED = "; the ledger's mapping is not confirmed"


def test_hash(test: ForensicTest, values: dict[str, Any], threshold: Any, data_hash: str) -> str:
    """What a run of the test depends on, as one sha256."""
    used = {key: forensic_settings.show(values[key]) for key in test.settings}
    text = json.dumps([test.name, used, str(threshold), data_hash], sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def choose(engagement: dict[str, Any], test: str | None, period: str | None) -> tuple[list, list]:
    """(tests, periods) to run. E991 for an unknown test or a period not in the engagement."""
    known, periods = tests(), periods_of(engagement)
    if test is not None and test not in known:
        raise ForensicTestUnknown(
            f"no forensic test {test!r}; the tests are {', '.join(known)}"
        ).with_context(test=test)
    if period is not None and period not in periods:
        raise ForensicTestUnknown(
            f"{period} is not a period of the open engagement ({', '.join(periods)}); nothing run"
        ).with_context(period=period)
    chosen = [known[test]] if test else list(known.values())
    return chosen, [period] if period else periods


def _outcome(test: ForensicTest, ctx: Context, books: Books) -> Outcome:
    missing = [k for k in test.kinds if k not in books.loaded.get(ctx.period, set())]
    if missing:
        names = " and ".join(KIND_NAMES.get(k, k) for k in missing)
        return Outcome.cannot(f"a loaded {names} for {ctx.period}")
    unset = [key for key in test.settings if ctx.settings[key] is None]
    if unset:
        return Outcome.cannot(f"{', '.join(unset)} on Settings (`fin forensic set`)")
    outcome = test.run(ctx)
    left_out = books.bank.left_out(ctx.period) if books.bank and "bank" in test.kinds else None
    if left_out:  # named whatever the outcome, also when it was the period's only statement
        outcome.needs = "; ".join(filter(None, [outcome.needs, left_out]))
    return outcome


def _store(
    con: duckdb.DuckDBPyConnection,
    run: Run,
    context: tuple[dict[str, Any], Books, Parties],
    test: ForensicTest,
    ctx: Context,
    outcome: Outcome,
) -> dict[str, Any]:
    engagement, books, parties = context
    used = {key: forensic_settings.show(ctx.settings[key]) for key in test.settings}
    used["query_threshold"] = str(ctx.threshold)
    over = [f.amount is None or abs(f.amount) >= ctx.threshold for f in outcome.flags]
    row = con.execute(
        "INSERT INTO forensic_runs (engagement_id, test, period, outcome, population, minimum, "
        "needs, not_whole, settings, flagged, over_threshold, near_misses, input_hash, run_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING forensic_run_id",
        [
            engagement["engagement_id"],
            test.name,
            ctx.period,
            outcome.outcome,
            outcome.population,
            outcome.minimum,
            outcome.needs,
            outcome.not_whole,
            json.dumps(used, sort_keys=True),
            len(outcome.flags),
            sum(over),
            outcome.near_misses,
            test_hash(test, ctx.settings, ctx.threshold, books.hash_for(test)),
            run.run_id,
            datetime.now(UTC).replace(tzinfo=None),
        ],
    ).fetchone()
    if row is None:  # pragma: no cover - RETURNING always gives the row
        raise RuntimeError("no forensic run id returned")
    for flag, is_over in zip(outcome.flags, over, strict=True):
        account = flag.account or books.accounts.get(flag.ledger or "")
        confirmed = account is not None if flag.ledger and not flag.account else None
        reason = flag.reason + (UNCONFIRMED if confirmed is False else "")
        con.execute(
            'INSERT INTO forensic_flags (forensic_run_id, test, period, file_id, sheet, "row", '
            "page, source, voucher_type, voucher_no, entry_date, ledger, account, "
            "mapping_confirmed, party, amount, reason, over_threshold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row[0],
                test.name,
                ctx.period,
                flag.file_id,
                flag.sheet,
                flag.row,
                flag.page,
                flag.source,
                flag.voucher_type,
                flag.voucher_no,
                flag.entry_date,
                flag.ledger,
                account or flag.ledger,
                confirmed,
                flag.party or parties.token(flag.ledger),
                flag.amount,
                reason,
                is_over,
            ],
        )
    return {
        "forensic_run_id": row[0],
        "test": test.name,
        "period": ctx.period,
        "outcome": outcome.outcome,
        "population": outcome.population,
        "minimum": outcome.minimum,
        "needs": outcome.needs,
        "not_whole": outcome.not_whole,
        "flagged": len(outcome.flags),
        "over_threshold": sum(over),
        "near_misses": outcome.near_misses,
        "settings": used,
    }


def run_tests(
    run: Run, root: Path, company: str, test: str | None = None, period: str | None = None
) -> list[dict[str, Any]]:
    """Run the tests (default all) for the periods (default the engagement's), in one transaction.
    E803 without an open engagement, E990 for an invalid [forensic] table, E991 for an unknown
    test or period."""
    values = forensic_settings.resolve(root, company)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        chosen, periods = choose(engagement, test, period)
        books = load(con, engagement)
        threshold = Decimal(engagement["query_threshold"])
        context = (engagement, books, Parties(con))
        results = []
        for t in chosen:
            for p in periods:
                ctx = Context(books, p, values, threshold)
                results.append(_store(con, run, context, t, ctx, _outcome(t, ctx, books)))
    for result in results:
        run.event("forensic", log_line(result), context={"company": company, **result})
    return results


def log_line(result: dict[str, Any]) -> str:
    """'je_round FY25: ran, 3 flagged (3 at or above the threshold) of 512; 1 just outside'."""
    head = f"{result['test']} {result['period']}: "
    if result["outcome"] == "cannot_run":
        return head + f"cannot run: needs {result['needs']}"
    if result["outcome"] == "too_few":
        return head + f"too few entries: {result['population']} of {result['minimum']} needed"
    text = head + (
        f"ran, {result['flagged']} flagged ({result['over_threshold']} at or above the "
        f"threshold) of {result['population']}; {result['near_misses']} just outside"
    )
    return text + (f"; cannot run for {result['needs']}" if result["needs"] else "")
