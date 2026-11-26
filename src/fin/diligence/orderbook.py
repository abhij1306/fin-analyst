"""Orderbook (docs/fdd.md F7): rows of `fin ingest <co> <file> --kind orderbook`, and what to show.

An orderbook lists the customer orders as at a date (title line "Orderbook as at 31-Mar-2025"), read
with a column profile (`templates/profiles/orderbook.toml`, `fin.extract.ledger`). Its period is the
engagement period (or the stub) that contains the as-at date, as for a payroll snapshot. Customer
names are stored as tokens (the registers' tokens: the same customer is the same token). Order
value and billed to date are kept in the order's currency; only INR orders count in rupees. A blank
billed-to-date cell is NULL (counted as not billed, and shown); a row with other cells but no
customer, order number or value, or an unreadable date or amount, is E960.
"""

from datetime import date
from typing import Any

import duckdb

from fin.audit.errors import OrderbookRowInvalid
from fin.diligence.loading import clean_text
from fin.diligence.mask import Masker
from fin.diligence.payroll import money
from fin.extract.ledger import Table, parse_date
from fin.extract.profiles import Profile, norm

COLUMNS = (
    'file_id, sheet, "row", as_at, customer, order_no, order_date, value, currency, start_date, '
    "end_date, billed, run_id"
)


def _day(value: Any, profile: Profile, where: str) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    found = parse_date(value, profile)
    if found is None:
        raise OrderbookRowInvalid(
            f"{where}: {str(value).strip()!r} is not a date (profile {profile.name} reads "
            f"{profile.date_format})"
        )
    return found


def rows(table: Table, file: dict[str, Any], run_id: str, masker: Masker) -> list[list[Any]]:
    """The `orderbook_lines` values (`COLUMNS`) of the table's rows. E960 for a row with no
    customer, order number or order value, or a bad date; E204 for an amount that is not a
    number."""
    profile, out = table.profile, []
    for r in table.rows:
        cells = r.cells
        customer, order_no = clean_text(cells.get("customer")), clean_text(cells.get("order_no"))
        if any(t is not None and norm(t) in profile.skip for t in (customer, order_no)):
            continue
        where = f"{file['file_name']} {r.sheet}!row {r.row}"
        value = money(cells.get("value"), profile, f"{where} value")
        if customer is None or order_no is None or value is None:
            what = "value"
            if customer is None:
                what = "customer"
            elif order_no is None:
                what = "order number"
            raise OrderbookRowInvalid(
                f"{where}: no {what} on a row with other cells; fin cannot use it. Nothing loaded"
            )
        currency = (clean_text(cells.get("currency")) or profile.currency).upper()
        out.append(
            [file["file_id"], r.sheet, r.row, table.end, masker.token("customer", customer)]
            + [order_no, _day(cells.get("order_date"), profile, f"{where} order date"), value]
            + [currency, _day(cells.get("start_date"), profile, f"{where} start date")]
            + [_day(cells.get("end_date"), profile, f"{where} end date")]
            + [money(cells.get("billed"), profile, f"{where} billed"), run_id]
        )
    return out


def summary(con: duckdb.DuckDBPyConnection, file: dict[str, Any], masker: Masker) -> list[str]:
    """What to show after an orderbook load: orders, masking and what fin cannot count."""
    row = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE currency <> 'INR'), "
        "count(*) FILTER (WHERE billed IS NULL), count(*) FILTER (WHERE end_date IS NULL) "
        "FROM orderbook_lines WHERE file_id = ?",
        [file["file_id"]],
    ).fetchone()
    assert row is not None
    count, foreign, blank, no_end = row
    lines = [
        f"orderbook {file['period']}: {count} orders as at {file['period_end'].isoformat()}, "
        f"{masker.added} names newly masked"
    ]
    for n, what in (
        (foreign, "orders not in INR (not converted: no rupee amount)"),
        (blank, "orders with billed to date blank (counted as not billed)"),
        (no_end, "orders with no end date (backlog not spread over a term)"),
    ):
        if n:
            lines.append(f"{n} {what}")
    return lines
