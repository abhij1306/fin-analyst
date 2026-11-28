"""The GST reconciliations (docs/fdd.md F6), one line per return month and measure (level 'gst',
key '2024-05 taxable_value'): a line at or above the threshold is for a query (`fin query raise
--items`), as for the D4 reconciliations. Left is the return; difference = left - right.

- gstr1_vs_sales_register: GSTR-1 taxable value vs the sales register's Amount in Rs (before GST)
  by invoice month;
- gstr3b_vs_gstr1: GSTR-3B outward taxable value and output tax (IGST + CGST + SGST + cess) vs
  GSTR-1's;
- gstr3b_vs_books: GSTR-3B output tax vs the credits on the confirmed gst_output ledgers, and
  GSTR-3B ITC vs the debits on the confirmed gst_input ledgers, by entry month in Ledger Vouchers;
- rcm: GSTR-3B RCM liability vs the credits on the confirmed rcm ledgers.

The books side is the gross credits (output, RCM) or debits (input) of the month, so a set-off
journal (output debited, input credited) or a tax payment does not hide the tax booked; a credit
note's tax is a debit on the output ledger and shows as a difference to query
(docs/decisions/fdd-f6.md). Nothing is changed to make a line tie. A return month whose figure is
blank (or, for output tax, any of its four taxes) is unknown: its line is not_reconciled, naming
the row, never compared as if the blank were 0.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile.base import ZERO, Computed, loaded_file, make_line, require_file, rows_text

GSTR1_VS_SALES = "gstr1_vs_sales_register"
GSTR3B_VS_GSTR1 = "gstr3b_vs_gstr1"
GSTR3B_VS_BOOKS = "gstr3b_vs_books"
RCM = "rcm"
NAMES = (GSTR1_VS_SALES, GSTR3B_VS_GSTR1, GSTR3B_VS_BOOKS, RCM)
LEVEL = "gst"
TAX = "igst + cgst + sgst + cess"
LEFT = {  # the names `fin reconcile` gives each side
    GSTR1_VS_SALES: ("GSTR-1", "register"),
    GSTR3B_VS_GSTR1: ("GSTR-3B", "GSTR-1"),
    GSTR3B_VS_BOOKS: ("GSTR-3B", "books"),
    RCM: ("GSTR-3B", "books"),
}
# month -> (amount, source rows); a return's amount is None when the return leaves it blank
Side = dict[date, tuple[Decimal | None, list[tuple[str, str, int]]]]


def _returns(con: duckdb.DuckDBPyConnection, found: tuple[Any, ...], column: str) -> Side:
    """month -> (the return's column, its row) for a loaded return file. A blank figure (and a
    sum with one) is None: unknown, never 0."""
    file_id, file_name = found[0], found[1]
    return {
        month: (None if value is None else Decimal(value), [(file_name, sheet, row)])
        for month, sheet, row, value in con.execute(
            f'SELECT month, sheet, "row", {column} FROM gst_returns WHERE file_id = ?', [file_id]
        ).fetchall()
    }


def _add(side: Side, month: date, value: Decimal, ref: tuple[str, str, int]) -> None:
    total, refs = side.get(month, (ZERO, []))
    side[month] = ((total or ZERO) + value, [*refs, ref])


def _lines(measure: str, left: Side, right: Side, note: str) -> list[dict[str, Any]]:
    out = []
    for month in sorted(set(left) | set(right)):
        lv, lr = left.get(month, (None, []))
        rv, rr = right.get(month, (None, []))
        blank = [ref for refs, value in ((lr, lv), (rr, rv)) if value is None for ref in refs]
        text = note
        if blank:  # a return row whose figure is blank: unknown, not compared as 0
            text = f"not compared: the figure is blank in the return ({rows_text(blank)}); {note}"
        out.append(
            make_line(
                LEVEL,
                f"{month:%Y-%m} {measure}",
                lv,
                rv,
                rows_text(lr),
                rows_text(rr),
                kind="not_reconciled" if blank else None,
                note=text,
            )
        )
    return out


def _sales(con: duckdb.DuckDBPyConnection, engagement_id: int, start: date, end: date) -> Side:
    side: Side = {}
    for month, file_name, sheet, row, value in con.execute(
        "SELECT date_trunc('month', r.invoice_date)::DATE, f.file_name, r.sheet, r.\"row\", "
        "r.amount_inr FROM register_lines r JOIN source_files f USING (file_id) "
        "WHERE f.engagement_id = ? AND f.kind = 'registers' AND f.status = 'loaded' "
        "AND r.kind = 'sales' AND r.invoice_date BETWEEN ? AND ? "
        'ORDER BY f.file_id, r.sheet, r."row"',
        [engagement_id, start, end],
    ).fetchall():
        _add(side, month, Decimal(value), (file_name, sheet, row))
    return side


def _gstr1(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> Any:
    return loaded_file(con, engagement["engagement_id"], "gstr1", period)


def applies_sales(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    found = _gstr1(con, engagement, period)
    return found is not None and bool(_sales(con, engagement["engagement_id"], found[2], found[3]))


def gstr1_vs_sales_register(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    found = require_file(con, company, engagement["engagement_id"], "gstr1", period, "GSTR-1")
    sales = _sales(con, engagement["engagement_id"], found[2], found[3])
    if not sales:
        raise ReconCannotRun(
            f"{company} {period}: no loaded sales register line in the period "
            f"(`fin ingest {company} <file> --kind registers`); nothing recorded"
        )
    first = con.execute(
        "SELECT min(file_id) FROM source_files WHERE engagement_id = ? AND kind = 'registers' "
        "AND status = 'loaded'",
        [engagement["engagement_id"]],
    ).fetchone()
    note = "GSTR-1 taxable value vs the sales register's Amount in Rs (before GST), invoice month"
    lines = _lines("taxable_value", _returns(con, found, "taxable_value"), sales, note)
    return Computed(lines, found[0], first[0] if first else None)


def applies_returns(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str
) -> bool:
    eid = engagement["engagement_id"]
    return all(loaded_file(con, eid, kind, period) for kind in ("gstr1", "gstr3b"))


def gstr3b_vs_gstr1(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    eid = engagement["engagement_id"]
    three_b = require_file(con, company, eid, "gstr3b", period, "GSTR-3B")
    one = require_file(con, company, eid, "gstr1", period, "GSTR-1")
    lines = _lines(
        "taxable_value",
        _returns(con, three_b, "taxable_value"),
        _returns(con, one, "taxable_value"),
        "GSTR-3B outward taxable value vs GSTR-1",
    ) + _lines(
        "output_tax",
        _returns(con, three_b, TAX),
        _returns(con, one, TAX),
        "GSTR-3B output tax (IGST + CGST + SGST + cess) vs GSTR-1",
    )
    lines.sort(key=lambda line: line["key"])
    return Computed(lines, three_b[0], one[0])


def _confirmed(con: duckdb.DuckDBPyConnection, tax_class: str) -> list[str]:
    return [
        ledger
        for (ledger,) in con.execute(
            "SELECT ledger FROM tax_ledgers WHERE status = 'confirmed' AND tax_class = ? "
            "ORDER BY ledger",
            [tax_class],
        ).fetchall()
    ]


def _books(
    con: duckdb.DuckDBPyConnection, voucher: tuple[Any, ...], tax_class: str, credits: bool
) -> Side:
    """month -> the credits (or debits) on the class's confirmed ledgers in the Ledger Vouchers."""
    side: Side = {}
    ledgers = _confirmed(con, tax_class)
    if not ledgers:
        return side
    for day, sheet, row, debit, credit in con.execute(
        'SELECT entry_date, sheet, "row", debit, credit FROM ledger_entries WHERE file_id = ? '
        'AND list_contains(?::VARCHAR[], ledger) ORDER BY sheet, "row"',
        [voucher[0], ledgers],
    ).fetchall():
        value = credit if credits else debit
        if value:
            _add(side, day.replace(day=1), Decimal(value), (voucher[1], sheet, row))
    return side


def _need(company: str, period: str, classes: tuple[str, ...]) -> ReconCannotRun:
    return ReconCannotRun(
        f"{company} {period}: no ledger is confirmed as {' or '.join(classes)}; Claude proposes "
        f"tax classes (`fin map tax {company} <file>`) and the user confirms them; nothing recorded"
    )


def applies_books(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    eid = engagement["engagement_id"]
    return all(loaded_file(con, eid, kind, period) for kind in ("gstr3b", "ledger_vouchers"))


def gstr3b_vs_books(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    eid = engagement["engagement_id"]
    three_b = require_file(con, company, eid, "gstr3b", period, "GSTR-3B")
    voucher = require_file(con, company, eid, "ledger_vouchers", period, "Ledger Vouchers")
    for tax_class in ("gst_output", "gst_input"):
        if not _confirmed(con, tax_class):
            raise _need(company, period, (tax_class,))
    lines = _lines(
        "output_tax",
        _returns(con, three_b, TAX),
        _books(con, voucher, "gst_output", credits=True),
        "GSTR-3B output tax vs the credits on the GST output ledgers in the month",
    ) + _lines(
        "itc",
        _returns(con, three_b, "itc"),
        _books(con, voucher, "gst_input", credits=False),
        "GSTR-3B ITC claimed vs the debits on the GST input ledgers in the month",
    )
    lines.sort(key=lambda line: line["key"])
    return Computed(lines, three_b[0], voucher[0])


def applies_rcm(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """GSTR-3B and Ledger Vouchers loaded, and the return has an RCM liability or a ledger is
    confirmed as rcm."""
    if not applies_books(con, engagement, period):
        return False
    three_b = loaded_file(con, engagement["engagement_id"], "gstr3b", period)
    assert three_b is not None
    liability = any(v for v, _ in _returns(con, three_b, "rcm_liability").values())
    return liability or bool(_confirmed(con, "rcm"))


def rcm(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    eid = engagement["engagement_id"]
    three_b = require_file(con, company, eid, "gstr3b", period, "GSTR-3B")
    voucher = require_file(con, company, eid, "ledger_vouchers", period, "Ledger Vouchers")
    if not _confirmed(con, "rcm"):
        raise _need(company, period, ("rcm",))
    lines = _lines(
        "rcm_liability",
        _returns(con, three_b, "rcm_liability"),
        _books(con, voucher, "rcm", credits=True),
        "GSTR-3B RCM liability vs the credits on the RCM ledgers in the month",
    )
    return Computed(lines, three_b[0], voucher[0])
