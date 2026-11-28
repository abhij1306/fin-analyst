"""The FY25 GSTR-2B (SPEC-20), as the GST portal's Excel download lays it out.

A preamble names the statement and its period, then a two-row header: a group cell ("Invoice
details", "Tax Amount") merged over its columns, the others merged down both rows; data from row 7.
Sheet B2B holds the suppliers' invoices, B2B-CDNR their credit and debit notes (none here). Dates
are text, dd-mm-yyyy, as the portal writes them. The rows are the purchase register's FY25
invoices with GST as the suppliers reported them, with the differences in `data_counterparty`.
"""

from datetime import date

from mock.generator import books as b
from mock.generator import data as d
from mock.generator import data_counterparty as cp
from mock.generator.sources import Cell, Sheet

GROUPS = {
    "B2B": (
        "Invoice details",
        ("Invoice number", "Invoice type", "Invoice Date", "Invoice Value(₹)"),
    ),
    "B2B-CDNR": (
        "Credit note/Debit note details",
        ("Note number", "Note type", "Note date", "Note Value (₹)"),
    ),
}
TAX = ("Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)")
AFTER = (
    "GSTR-1/IFF/GSTR-5 Period",
    "GSTR-1/IFF/GSTR-5 Filing Date",
    "ITC Availability",
    "Reason",
    "Applicable % of Tax Rate",
)


def _text(day: date) -> str:
    return f"{day:%d-%m-%Y}"


def _sheet(title: str) -> Sheet:
    group, details = GROUPS[title]
    start, end = d.PERIODS[cp.TWO_B_PERIOD]
    s = Sheet(title)
    for line in (
        "Goods and Services Tax - GSTR-2B",
        "GSTIN 27AACCC1234F1Z5",
        f"Legal name {d.COMPANY}",
        f"Statement period {_text(start)} to {_text(end)}",
    ):
        s.add([line])
    top: list[Cell] = ["GSTIN of supplier", "Trade/Legal name", group, None, None, None]
    top += ["Place of supply", "Supply Attract Reverse Charge", "Taxable Value (₹)", "Tax Amount"]
    top += [None, None, None, *AFTER]
    s.add(top)
    s.add([None, None, *details, None, None, None, *TAX, *([None] * len(AFTER))])
    for column in "ABGHINOPQR":
        s.merges.append(f"{column}5:{column}6")
    s.merges += ["C5:F5", "J5:M5"]
    return s


def gstr2b(books: b.Books) -> list[Sheet]:
    start, end = d.PERIODS[cp.TWO_B_PERIOD]
    invoices = [
        inv
        for inv in books.invoices
        if inv.kind == "purchase"
        and start <= inv.when <= end
        and inv.number not in cp.TWO_B_MISSING
    ]
    s = _sheet("B2B")
    for inv in sorted(invoices, key=lambda i: (i.when, i.number)):
        taxable = cp.TWO_B_TAXABLE.get(inv.number, inv.amount_inr)
        tax = taxable * d.GST_RATE // 100
        filed = date(inv.when.year + (inv.when.month == 12), inv.when.month % 12 + 1, 11)
        s.add(
            [
                cp.GSTIN[inv.party],
                cp.TWO_B_NAMES.get(inv.party, inv.party),
                inv.number,
                "Regular",
                _text(inv.when),
                taxable + 2 * tax,
                "Maharashtra",
                "No",
                taxable,
                0,
                tax,
                tax,
                0,
                f"{inv.when:%m%Y}",
                _text(filed),
                "Yes",
                None,
                None,
            ]
        )
    return [s, _sheet("B2B-CDNR")]
