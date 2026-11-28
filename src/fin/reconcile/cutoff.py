"""sales_cutoff and purchase_cutoff (docs/fdd.md F3): cut-off tests on the registers at a year end.

sales_cutoff: sales register lines invoiced within `[fdd] cutoff_days` of the year end whose
service is on the other side of it: invoiced on or before the year end for a service that starts
after it (billed ahead), or invoiced after it for a service that ended on or before it (billed
late).
purchase_cutoff: purchase register lines invoiced within `[fdd] unrecorded_days` after the year end
for a service that ended on or before it (a possible unrecorded liability).
In both, a line invoiced after the year end whose service started on or before it and ends after
it straddles the year end: it is its own line (key suffix "(straddles)" when the invoice also has
lines flagged in full), and its amount is the part for the service days up to the year end, by
days, computed in SQL. A line invoiced after the year end with a service start on or before it and
no service end is flagged in full, its note saying the end is not known.

The windows are the engagement's `cutoff_days` and `unrecorded_days`, copied from config.toml [fdd]
when it opens (like the bank matching settings), and the year end comes from its fiscal start month.

A line is one invoice of one party (its flagged register lines added up, Amount in Rs), keyed by its
number (with the party token when two parties use the same number): kind left_only,
level invoice, left_amount the amount, no right side; at or above the threshold it needs a query
(`fin query raise --items`). What cannot be tested is listed as not_reconciled lines at level
'coverage': register lines in the window with no service start date, and registers that end before
the window does. Nothing here changes company data; whether a line is a real cut-off error is the
company's to explain.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import CutoffCannotRun
from fin.db.rows import fetch_one
from fin.diligence.stub import fiscal_start
from fin.reconcile.base import ZERO, Computed, make_line, rows_text

SALES = "sales_cutoff"
PURCHASE = "purchase_cutoff"
NAMES = (SALES, PURCHASE)


@dataclass(frozen=True)
class Window:
    kind: str  # register kind: sales or purchase
    before: int  # days before the year end tested
    after: int  # days after the year end tested
    key: str  # the [fdd] key the days come from


def _settings(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], name: str, period: str
) -> tuple[date, Window] | None:
    """(year end, window) of an annual period of the engagement, None for another period."""
    row = con.execute(
        "SELECT periods, fiscal_start_month, cutoff_days, unrecorded_days FROM engagement "
        "WHERE engagement_id = ?",
        [engagement["engagement_id"]],
    ).fetchone()
    if row is None or period not in row[0].split(","):
        return None
    start = fiscal_start(2000 + int(period[2:]), row[1])
    year_end = date(start.year + 1, start.month, 1) - timedelta(days=1)
    if name == SALES:
        return year_end, Window("sales", row[2], row[2], "cutoff_days")
    return year_end, Window("purchase", 0, row[3], "unrecorded_days")


# Columns: file_id, file_name, sheet, row, invoice_no, invoice_date, service_start, amount_inr,
# party, service_end, and before_year_end: for a service that runs across the year end, the share
# of the amount for its service days up to the year end (by days), else NULL.
_SELECT = """
WITH y AS (SELECT ?::DATE AS year_end)
SELECT r.file_id, s.file_name, r.sheet, r."row", r.invoice_no, r.invoice_date, r.service_start,
    r.amount_inr, CASE r.kind WHEN 'sales' THEN r.customer ELSE r.vendor END, r.service_end,
    CASE WHEN r.service_start <= y.year_end AND r.service_end > y.year_end
        THEN CAST(round(r.amount_inr * (date_diff('day', r.service_start, y.year_end) + 1)
            / (date_diff('day', r.service_start, r.service_end) + 1), 2) AS DECIMAL(20, 2)) END
FROM register_lines r JOIN source_files s USING (file_id), y
WHERE s.engagement_id = ? AND s.status = 'loaded' AND r.kind = ?
  AND r.invoice_date BETWEEN ? AND ?
ORDER BY r.invoice_date, r.file_id, r.sheet, r."row"
"""


def _rows(
    con: duckdb.DuckDBPyConnection,
    engagement_id: int,
    kind: str,
    year_end: date,
    start: date,
    end: date,
) -> list[Any]:
    return con.execute(_SELECT, [year_end, engagement_id, kind, start, end]).fetchall()


def _applies(name: str) -> Any:
    def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
        found = _settings(con, engagement, name, period)
        if found is None:
            return False
        year_end, window = found
        start = year_end - timedelta(days=window.before)
        end = year_end + timedelta(days=window.after)
        return bool(_rows(con, engagement["engagement_id"], window.kind, year_end, start, end))

    return applies


STRADDLES = "straddles"


def _flag(window: Window, year_end: date, row: Any) -> tuple[str, str] | None:
    """(how it is flagged: 'full' or STRADDLES, why) for a register line, or None."""
    day, service, service_end = row[5], row[6], row[9]
    if day <= year_end and service > year_end and window.kind == "sales":
        return "full", (
            f"invoiced {day}, on or before the year end {year_end}, for a service that starts on "
            f"{service}, after it: billed ahead of the service"
        )
    if day <= year_end or service > year_end:
        return None
    if service_end is not None and service_end > year_end:
        return STRADDLES, (
            f"invoiced {day}, after the year end {year_end}, for a service from {service} to "
            f"{service_end}: straddles the year end; the amount is the part for the service days "
            "up to the year end (by days)"
        )
    performed = f"ended on {service_end}" if service_end is not None else "has no service end date"
    if window.kind == "sales":
        return "full", (
            f"invoiced {day}, after the year end {year_end}, for a service that started on "
            f"{service} and {performed}: billed late"
        )
    return "full", (
        f"invoiced {day}, within {window.after} days after the year end {year_end}, for a "
        f"service that started on {service} and {performed}: is the liability in the year's "
        "books?"
    )


def _compute(name: str) -> Any:
    def compute(
        con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
    ) -> Computed:
        found = _settings(con, engagement, name, period)
        if found is None:
            raise CutoffCannotRun(
                f"{period} is not an annual period of {company}'s open engagement; {name} is "
                "tested at a year end. Nothing recorded"
            )
        year_end, window = found
        start = year_end - timedelta(days=window.before)
        end = year_end + timedelta(days=window.after)
        rows = _rows(con, engagement["engagement_id"], window.kind, year_end, start, end)
        if not rows:
            raise CutoffCannotRun(
                f"{company} {period}: no loaded {window.kind} register line is dated {start} to "
                f"{end} (`fin ingest {company} <file> --kind registers`); nothing recorded"
            )
        flagged: dict[tuple[str, str | None, str], list[Any]] = {}
        notes: dict[tuple[str, str | None, str], str] = {}
        undated = []
        for row in rows:
            if row[6] is None:
                undated.append(row)
                continue
            if (found := _flag(window, year_end, row)) is not None:
                key = (row[4], row[8], found[0])
                flagged.setdefault(key, []).append(row)
                notes.setdefault(key, found[1])
        lines = [
            make_line(
                "invoice",
                _key(flagged, number, party, how),
                sum((Decimal(r[10] if how == STRADDLES else r[7]) for r in found), ZERO),
                None,
                rows_text([(r[1], r[2], r[3]) for r in found]),
                None,
                note=notes[(number, party, how)],
            )
            for (number, party, how), found in flagged.items()
        ]
        lines += _coverage(con, engagement, window, end, undated)
        return Computed(lines, min(r[0] for r in rows), None)

    return compute


def _key(
    flagged: dict[tuple[str, str | None, str], list[Any]], number: str, party: str | None, how: str
) -> str:
    """The line key: the invoice number, with the party token when two parties use it, and
    '(straddles)' when the same invoice also has lines flagged in full."""
    parties = {p for n, p, _ in flagged if n == number}
    key = number if len(parties) == 1 else f"{number} ({party})"
    if how == STRADDLES and (number, party, "full") in flagged:
        key += " (straddles)"
    return key


def _coverage(
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    window: Window,
    end: date,
    undated: list[Any],
) -> list[dict[str, Any]]:
    """The not_reconciled lines: what the window could not test."""
    out = []
    if undated:
        out.append(
            make_line(
                "coverage",
                "no service start",
                sum((Decimal(r[7]) for r in undated), ZERO),
                None,
                rows_text([(r[1], r[2], r[3]) for r in undated]),
                None,
                kind="not_reconciled",
                note=f"{len(undated)} {window.kind} register lines in the window have no service "
                "start date: not tested",
            )
        )
    last = fetch_one(
        con,
        "SELECT max(r.invoice_date) FROM register_lines r JOIN source_files s USING (file_id) "
        "WHERE s.engagement_id = ? AND s.status = 'loaded' AND r.kind = ?",
        [engagement["engagement_id"], window.kind],
    )[0]
    if last is not None and last < end:
        out.append(
            make_line(
                "coverage",
                "register end",
                None,
                None,
                None,
                None,
                kind="not_reconciled",
                note=f"the loaded {window.kind} registers end on {last}, before {end} (the year "
                f"end plus the engagement's {window.key} = {window.after} days): later invoices "
                "are not tested",
            )
        )
    return out


sales_cutoff = _compute(SALES)
purchase_cutoff = _compute(PURCHASE)
applies_sales = _applies(SALES)
applies_purchase = _applies(PURCHASE)
