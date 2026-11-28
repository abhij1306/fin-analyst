"""The engagement: one diligence job on one company (`fin engagement open|status|close`).

It holds the annual periods in scope, the optional current-year stub (`fin.diligence.stub`) and
the query threshold, and lives in the company database (table `engagement`,
db/schema/diligence.sql). At most one is open at a time; every other diligence command works on the
open one and stops with E803 when there is none.

Every engagement, query and adjustment added, and every status change, is appended to the audit
table `diligence_events` (`record_event`) in the same unit of work as the change, as `fin.findings`
does for findings. A command that changes them first backs up the company database to
`backups/<run_id>/` (`company_database(write=True)`, once per command), as `fin sql --write` does.
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Any

import duckdb

from fin.audit.errors import (
    CreditTermsInvalid,
    DependencyMissing,
    EngagementState,
    SourcePeriodInvalid,
)
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect, transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence.stub import UNAUDITED, parse_stub
from fin.paths import company_db_path
from fin.query.write import backup

ANNUAL = re.compile(r"^FY\d{2}$")
PERIOD = re.compile(r"^FY\d{2}(?:-(?:Q[1-4]|H[12]|9M))?$")  # fin.extract.periods names
QUERY_STATUSES = ("draft", "approved", "sent", "answered", "closed", "withdrawn")
ADJUSTMENT_STATUSES = ("proposed", "accepted", "rejected")
# kind in diligence_events -> (table, id column)
KINDS = {
    "engagement": ("engagement", "engagement_id"),
    "query": ("accountant_queries", "query_id"),
    "adjustment": ("adjustments", "adj_id"),
}  # 'gate' rows (approvals) are recorded by fin.diligence.gates


def parse_periods(text: str) -> list[str]:
    """'FY24,FY25,FY26' -> ['FY24', 'FY25', 'FY26']: annual periods, each once, in order. Raises
    ValueError otherwise."""
    periods = [part.strip() for part in text.split(",") if part.strip()]
    if not periods:
        raise ValueError("give at least one annual period, e.g. FY24,FY25,FY26")
    bad = [period for period in periods if not ANNUAL.match(period)]
    if bad:
        raise ValueError(f"not an annual period: {', '.join(bad)} (use e.g. FY24,FY25,FY26)")
    if len(set(periods)) != len(periods):
        raise ValueError(f"a period is given more than once: {text}")
    return sorted(periods)


AMOUNT_LIMIT = Decimal(10) ** 18  # adjustments.amount is DECIMAL(20,2)


def parse_rupees(text: str) -> Decimal:
    """An exact amount in rupees with at most two decimals and less than 10^18 in size (what a
    DECIMAL(20,2) column holds), e.g. '-350000' or '1250.50'. Raises ValueError otherwise
    (nothing is rounded)."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{text!r} is not an amount in rupees (e.g. 350000 or -1250.50)") from exc
    if amount.is_finite() and amount.copy_abs() >= AMOUNT_LIMIT:  # copy_abs cannot overflow
        raise ValueError(f"{text!r} is too large for an amount in rupees (below 10^18)")
    try:
        exact = amount.is_finite() and amount == amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        exact = False
    if not exact:
        raise ValueError(f"{text!r} is not an amount in rupees with at most two decimals")
    return amount


def rupees(value: Any) -> str:
    """An amount for display: ₹1,250.50 or -₹1,250.50, or '-' when there is none."""
    if value is None:
        return "-"
    amount = Decimal(str(value))
    return f"{'-' if amount < 0 else ''}₹{abs(amount):,}"


@contextmanager
def company_database(
    run: Run, company: str, *, write: bool = False
) -> Iterator[duckdb.DuckDBPyConnection]:
    """The company's database (it must exist: the company has documents loaded). With `write=True`
    it is backed up first (`backups/<run_id>/companies/<company>.duckdb`); each diligence command
    opens it for writing once."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        raise DependencyMissing(
            f"no database for company {company!r}; ingest its documents first (`fin ingest`)"
        )
    ensure_database(path, "company")
    if write:
        backup(run.root, path, run)
    with connect(path, write=write) as con:
        yield con


def record_event(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, kind: str, item_id: int
) -> None:
    """Append the item, as it is now, to the audit table `diligence_events`."""
    table, column = KINDS[kind]
    [row] = fetch_dicts(con, f"SELECT * FROM {table} WHERE {column} = ?", [item_id])
    run.record(
        "diligence_events",
        {
            "company": company,
            "engagement_id": row["engagement_id"],
            "kind": kind,
            "item_id": str(item_id),
            "status": row["status"],
            "detail": json.dumps(row, default=str),
        },
    )


def open_engagement_row(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    rows = fetch_dicts(con, "SELECT * FROM engagement WHERE status = 'open'")
    return rows[0] if rows else None


def is_open(run: Run, company: str) -> bool:
    """Whether the company has an open engagement (the list commands say so instead of E803)."""
    with company_database(run, company) as con:
        return open_engagement_row(con) is not None


def require_open(con: duckdb.DuckDBPyConnection, company: str) -> dict[str, Any]:
    """The open engagement. E803 when there is none."""
    row = open_engagement_row(con)
    if row is None:
        raise EngagementState(
            f"{company} has no open engagement; nothing changed. Open one with "
            f"`fin engagement open {company} --periods FY24,FY25,FY26`"
        )
    return row


MAX_DUE_DAYS = 3650  # as config.toml [diligence] invoice_due_days


def check_due_days(company: str, days: int) -> int:
    """The credit terms in days. E846 unless whole days from 0 to MAX_DUE_DAYS."""
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_DUE_DAYS:
        raise CreditTermsInvalid(
            f"{company}: credit terms of {days!r} days; give whole days from 0 to {MAX_DUE_DAYS}. "
            "Nothing changed"
        ).with_context(due_days=days)
    return days


def open_engagement(
    run: Run,
    company: str,
    periods: list[str],
    threshold: Decimal | None,
    stub: str | None = None,
    due_days: int | None = None,
) -> dict[str, Any]:
    """Open an engagement for the periods (and the stub, e.g. 'FY27-YTD-SEP'), with the threshold
    and the credit terms in days (`due_days`) given or config.toml's. E803 when one is already
    open; E808 for a stub `parse_stub` refuses; E846 for credit terms out of range."""
    config = load_config(run.root)
    if threshold is None:
        threshold = config.query_threshold
    due = config.invoice_due_days if due_days is None else check_due_days(company, due_days)
    try:
        ytd = parse_stub(stub, periods, config.fiscal_year_start_month) if stub else None
    except ValueError as exc:
        raise SourcePeriodInvalid(f"{company}: {exc}; nothing opened") from exc
    with company_database(run, company, write=True) as con, transaction(con):
        existing = open_engagement_row(con)
        if existing is not None:
            raise EngagementState(
                f"{company} already has an open engagement (#{existing['engagement_id']}, "
                f"periods {existing['periods']}); nothing changed. Close it first with "
                f"`fin engagement close {company}`"
            )
        now = utcnow()
        new = con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, "
            "match_window_days, fx_window_days, invoice_due_days, fiscal_start_month, cutoff_days, "
            "unrecorded_days, status, opened_run_id, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?) "
            "RETURNING engagement_id",
            [
                ",".join(periods),
                threshold,
                ytd and ytd.name,
                ytd and ytd.start,
                ytd and ytd.end,
                config.match_window_days,
                config.fx_window_days,
                due,
                config.fiscal_year_start_month,
                config.fdd["cutoff_days"],
                config.fdd["unrecorded_days"],
                run.run_id,
                now,
            ],
        ).fetchone()
        assert new is not None
        record_event(run, company, con, "engagement", new[0])
        return require_open(con, company)


def set_due_days(run: Run, company: str, days: int) -> dict[str, Any]:
    """Set the open engagement's credit terms (an unmatched invoice is overdue after `days`).
    Returns the engagement row and `before`, the earlier value. A bank_vs_invoices run made with
    other terms no longer matches its inputs, so gate G2 reopens until it runs again. E803 when no
    engagement is open; E846 for days out of range."""
    check_due_days(company, days)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        con.execute(
            "UPDATE engagement SET invoice_due_days = ? WHERE engagement_id = ?",
            [days, engagement_id],
        )
        record_event(run, company, con, "engagement", engagement_id)
        [row] = fetch_dicts(
            con, "SELECT * FROM engagement WHERE engagement_id = ?", [engagement_id]
        )
    return {"engagement": row, "before": engagement["invoice_due_days"]}


def format_due_days(company: str, result: dict[str, Any]) -> str:
    row, before = result["engagement"], result["before"]
    if before == row["invoice_due_days"]:
        return f"{company}: credit terms already {before} days; nothing changed"
    return (
        f"{company}: credit terms {before} -> {row['invoice_due_days']} days for engagement "
        f"#{row['engagement_id']}; run `fin reconcile {company} bank_vs_invoices` again "
        "(gate G2 reopens until it runs with the new terms)"
    )


def close_engagement(run: Run, company: str) -> dict[str, Any]:
    """Close the open engagement. E803 when there is none; E801 unless gate G4 (and every gate
    before it) is approved. An engagement with no queries, no adjustments and no FDD proposal
    waiting for the user closes without its gates: there is nothing to lose (one opened for periods
    without data could never close)."""
    from fin.diligence import gates  # gates imports this module
    from fin.fdd import gate_checks

    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        work = con.execute(
            "SELECT (SELECT count(*) FROM accountant_queries WHERE engagement_id = ?) "
            "+ (SELECT count(*) FROM adjustments WHERE engagement_id = ?)",
            [engagement_id, engagement_id],
        ).fetchone()
        assert work is not None
        ctx = gates.context(con, run.root, company)
        if work[0] or not all(ok for ok, _ in gate_checks.checklist(ctx)):
            gates.require(ctx, "G4", "fin engagement close")
        con.execute(
            "UPDATE engagement SET status = 'closed', closed_run_id = ?, closed_at = ? "
            "WHERE engagement_id = ?",
            [run.run_id, utcnow(), engagement_id],
        )
        record_event(run, company, con, "engagement", engagement_id)
        [row] = fetch_dicts(
            con, "SELECT * FROM engagement WHERE engagement_id = ?", [engagement_id]
        )
        return row


def engagement_status(run: Run, company: str) -> dict[str, Any]:
    """The open engagement, its queries counted by status and its adjustments counted and summed by
    status. E803 when there is none."""
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        queries = dict(
            con.execute(
                "SELECT status, count(*) FROM accountant_queries WHERE engagement_id = ? "
                "GROUP BY status",
                [engagement_id],
            ).fetchall()
        )
        adjustments = {
            status: (count, total)
            for status, count, total in con.execute(
                "SELECT status, count(*), sum(amount) FROM adjustments WHERE engagement_id = ? "
                "GROUP BY status",
                [engagement_id],
            ).fetchall()
        }
    zero = (0, Decimal("0.00"))
    return {
        "company": company,
        "engagement": engagement,
        "queries": {status: queries.get(status, 0) for status in QUERY_STATUSES},
        "adjustments": {
            status: {
                "count": adjustments.get(status, zero)[0],
                "amount": adjustments.get(status, zero)[1],
            }
            for status in ADJUSTMENT_STATUSES
        },
    }


def format_engagement(row: dict[str, Any]) -> str:
    line = (
        f"engagement #{row['engagement_id']} {row['status']}: periods {row['periods']}"
        + (f", stub {row['stub']} ({UNAUDITED})" if row.get("stub") else "")
        + f", query threshold {rupees(row['query_threshold'])}"
        + f", credit terms {row['invoice_due_days']} days"
        + f", opened {row['opened_at']:%Y-%m-%d}"
    )
    if row["closed_at"] is not None:
        line += f", closed {row['closed_at']:%Y-%m-%d}"
    return line


def format_status(result: dict[str, Any]) -> str:
    queries = ", ".join(f"{status} {n}" for status, n in result["queries"].items())
    adjustments = ", ".join(
        f"{status} {item['count']} ({rupees(item['amount'])})"
        for status, item in result["adjustments"].items()
    )
    return (
        f"{result['company']}: {format_engagement(result['engagement'])}\n"
        f"  queries: {queries}\n"
        f"  adjustments: {adjustments}"
    )
