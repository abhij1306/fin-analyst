"""orderbook_vs_register (docs/fdd.md F7): orders with no billing, billing with no order.

Left is the period's orderbook (the loaded file as at a date in the period), right is the sales
register's billing in the period, matched by customer token (the orderbook has an order number, the
register no order reference). Two kinds of line, level 'order':

- an order whose customer has no sales register line in the period: left_only, left the order
  value in rupees, the note the billed to date the orderbook says; an order not in INR counts at
  the rate the user stated for its currency as at the orderbook's date (`fin engagement rate`,
  view orderbook_valued), and with no such rate has no rupee amount (listed, never reaches the
  threshold);
- a customer (not a pass-through customer) with sales register billing in the period and no order
  on the orderbook: right_only, right the billing. If the orderbook lists open orders only, a
  finished order is not on it: the note says so (client question 10,
  docs/client-questions.md).

A line at or above the threshold can be grouped into a query (`fin query raise --items`). Nothing
here changes company data.
"""

from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile.base import Computed, count_text, make_line, require_file, rows_text

NAME = "orderbook_vs_register"

_BILLING = """
SELECT r.customer, sum(r.amount_inr), count(*), min(f.file_id), min(f.file_name), min(r.sheet)
FROM register_lines r JOIN source_files f ON f.file_id = r.file_id
WHERE f.engagement_id = ? AND f.kind = 'registers' AND f.status = 'loaded' AND r.kind = 'sales'
  AND r.period = ? AND r.customer IS NOT NULL
GROUP BY r.customer ORDER BY r.customer
"""


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """An orderbook file and sales register lines are loaded for the period."""
    row = con.execute(
        "SELECT (SELECT count(*) FROM source_files WHERE engagement_id = ? AND kind = 'orderbook' "
        "AND period = ? AND status = 'loaded'), (SELECT count(*) FROM register_lines r JOIN "
        "source_files f USING (file_id) WHERE f.engagement_id = ? AND f.kind = 'registers' "
        "AND f.status = 'loaded' AND r.kind = 'sales' AND r.period = ?)",
        [engagement["engagement_id"], period, engagement["engagement_id"], period],
    ).fetchone()
    return bool(row and row[0] and row[1])


def orderbook_vs_register(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """Orders with no billing and billing with no order for the period. E840 when the period has no
    loaded orderbook or no sales register line."""
    eid = engagement["engagement_id"]
    book = require_file(con, company, eid, "orderbook", period, "orderbook")
    billing = {row[0]: row for row in con.execute(_BILLING, [eid, period]).fetchall()}
    if not billing:
        raise ReconCannotRun(
            f"{company} {period}: no loaded sales register line (`fin ingest {company} <file> "
            "--kind registers`); nothing recorded"
        )
    orders = con.execute(
        'SELECT sheet, "row", customer, order_no, value, currency, billed, value_inr, rate_note '
        'FROM orderbook_valued WHERE file_id = ? ORDER BY sheet, "row"',
        [book[0]],
    ).fetchall()
    passthrough = {
        c
        for (c,) in con.execute(
            "SELECT customer FROM passthrough_customers WHERE engagement_id = ?", [eid]
        ).fetchall()
    }
    ordered = {o[2] for o in orders}
    lines = []
    for sheet, row, customer, order_no, value, currency, billed, value_inr, rate_note in orders:
        if customer in billing:
            continue
        shown = "blank" if billed is None else f"{currency} {billed:,.2f}"
        foreign = f"; order value {currency} {value:,.2f}; {rate_note}" if rate_note else ""
        lines.append(
            make_line(
                "order",
                f"{order_no} ({customer})",
                value_inr,
                None,
                rows_text([(book[1], sheet, row)]),
                None,
                kind="left_only",
                note=f"no sales register line for {customer} in {period}; billed to date per the "
                f"orderbook: {shown}{foreign}",
            )
        )
    for customer, (_, amount, count, _, file_name, sheet) in billing.items():
        if customer in ordered or customer in passthrough:
            continue
        lines.append(
            make_line(
                "order",
                f"billing {customer}",
                None,
                amount,
                None,
                count_text(file_name, sheet, count),
                kind="right_only",
                note=f"sales register billing in {period} and no order on the orderbook as at "
                f"{book[3].isoformat()}; if the orderbook lists open orders only, a finished "
                "order is not on it",
            )
        )
    first = min(row[3] for row in billing.values())
    return Computed(
        lines=sorted(lines, key=lambda x: (x["key"], x["kind"])),
        left_file_id=book[0],
        right_file_id=first,
    )
