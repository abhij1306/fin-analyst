"""Synthetic bank statements (made-up names and accounts): Excel for ICICI, SBI and Axis, and the
recorded output of a PDF parser for a synthetic Axis PDF.

`write_statement` lays out one statement the way `templates/profiles/bank_<bank>.toml` reads it:
header lines (account, currency, period, opening and closing balance), then the table. `rows` are
(day, remarks, debit, credit); balances run from `opening`. `closing` overrides the printed closing
balance (to plant a statement that does not tie).
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from tests.synthetic_pdf import Page, write_pdf

CUSTOMER = "Orchid Systems Pvt Ltd"
VENDOR = "Banyan Traders"
Row = tuple[int, str, int, int]  # day of month, remarks, debit, credit

# bank -> (date format, header text of the table, period line)
LAYOUT = {
    "icici": (
        "%d/%m/%Y",
        ["S No.", "Value Date", "Transaction Date", "Cheque Number", "Transaction Remarks"],
        "Transactions Period: {a} to {b}",
    ),
    "sbi": (
        "%d %b %Y",
        ["Txn Date", "Value Date", "Description", "Ref No./Cheque No."],
        "Statement from: {a} to {b}",
    ),
    "axis": (
        "%d-%m-%Y",
        ["Tran Date", "CHQNO", "PARTICULARS"],
        "Statement of Account From {a} To {b}",
    ),
}


def _header(bank: str, currency: str) -> list[str]:
    cur = f"({currency})"
    return {
        "icici": [
            "S No.",
            "Value Date",
            "Transaction Date",
            "Cheque Number",
            "Transaction Remarks",
            f"Withdrawal Amount {cur}",
            f"Deposit Amount {cur}",
            f"Balance {cur}",
        ],
        "sbi": ["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit"]
        + ["Balance"],
        "axis": ["Tran Date", "CHQNO", "PARTICULARS", "DR", "CR", "BAL", "SOL"],
    }[bank]


def table(
    bank: str, year: int, month: int, opening: int, rows: list[Row], currency: str
) -> list[list[object]]:
    """The statement's table: the header row, then a row per transaction (dates as text)."""
    fmt = LAYOUT[bank][0]
    out: list[list[object]] = [_header(bank, currency)]
    balance = opening
    for n, (day, remarks, debit, credit) in enumerate(rows, start=1):
        balance += credit - debit
        when = date(year, month, day).strftime(fmt)
        cheque = f"REF{n:04d}"
        if bank == "icici":
            out.append([n, when, when, cheque, remarks, debit or None, credit or None, balance])
        elif bank == "sbi":
            out.append([when, when, remarks, cheque, debit or None, credit or None, balance])
        else:
            out.append([when, cheque, remarks, debit or None, credit or None, balance, "100"])
    return out


def header_lines(
    bank: str, account: str, currency: str, start: date, end: date, opening: int, closing: int
) -> list[str]:
    fmt = LAYOUT[bank][0]
    a, b = start.strftime(fmt), end.strftime(fmt)
    name = {"icici": "ICICI Bank", "sbi": "State Bank of India", "axis": "Axis Bank"}[bank]
    currency_line = "Currency" if bank == "axis" else "Account Currency"
    return [
        f"{name} - Statement of Account",
        f"Account Number : {account}",
        f"{currency_line} : {currency}",
        LAYOUT[bank][2].format(a=a, b=b),
        f"Opening Balance : {opening:,.2f}",
        f"Closing Balance : {closing:,.2f}",
    ]


def write_statement(
    path: Path,
    bank: str = "icici",
    *,
    year: int = 2024,
    month: int = 4,
    last_day: int = 30,
    account: str = "123456789012",
    currency: str = "INR",
    opening: int = 100000,
    rows: list[Row] | None = None,
    closing: int | None = None,
) -> Path:
    rows = rows if rows is not None else DEFAULT_ROWS
    shown = opening + sum(c - d for _, _, d, c in rows)
    closing = shown if closing is None else closing
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Statement"
    for line in header_lines(
        bank, account, currency, date(year, month, 1), date(year, month, last_day), opening, closing
    ):
        sheet.append([line])
    for row in table(bank, year, month, opening, rows, currency):
        sheet.append(row)
    book.save(path)
    return path


DEFAULT_ROWS: list[Row] = [
    (3, f"NEFT-{CUSTOMER}-INV101", 0, 250000),
    (10, f"IMPS to {VENDOR} rent", 40000, 0),
    (22, "Bank charges", 590, 0),
]
CLOSING = 100000 + 250000 - 40000 - 590  # of DEFAULT_ROWS from the default opening

# The synthetic Axis PDF: two pages, the table split across them, the summary above it.
PDF_ROWS: list[Row] = [
    (2, f"NEFT-{CUSTOMER}-INV202", 0, 90000),
    (9, f"RTGS to {VENDOR}", 30000, 0),
    (17, "Cheque deposit", 0, 15000),
    (25, "Bank charges", 118, 0),
]


def pdf_pages(opening: int = 200000, misread: bool = False) -> dict[str, dict[str, object]]:
    """The saved parse of the synthetic Axis PDF as a PDF add-on returned it (recorded output):
    page 1 has the header text and the first rows, page 2 the rest without a header. `misread`:
    the parser read the 9 April debit as 3,000 (a figure lost a digit): the statement no longer
    ties."""
    grid = table("axis", 2024, 4, opening, PDF_ROWS, "INR")
    text = [[str(c) if c is not None else "" for c in row] for row in grid]
    if misread:
        text[2][3] = "3,000.00"
    closing = opening + sum(c - d for _, _, d, c in PDF_ROWS)
    lines = header_lines(
        "axis", "917020012345678", "INR", date(2024, 4, 1), date(2024, 4, 30), opening, closing
    )
    return {
        "1": {"tables": [text[:3]], "texts": lines},
        "2": {"tables": [text[3:]], "texts": ["Registered Office: Synthetic Tower, Mumbai"]},
    }


def write_bank_pdf(path: Path, tag: str = "") -> Path:
    """A synthetic two-page text PDF of the statement (what the parser's output is recorded from);
    `tag` makes another file (another sha256) of it."""
    pages: list[Page] = []
    for body in pdf_pages().values():
        texts = [str(t) for t in body["texts"]]  # type: ignore[attr-defined]
        pages.append([(40, 40 + 14 * n, line) for n, line in enumerate([*texts, tag])])
    return write_pdf(path, *pages)


def recorded_parse(sha256: str, *, misread: bool = False) -> str:
    """The saved parse (`cache/bank/<company>/<sha256>.json`) as JSON."""
    return json.dumps(
        {"engine": "liteparse", "sha256": sha256, "pages": pdf_pages(misread=misread)}
    )


def total(rows: list[Row]) -> Decimal:
    return Decimal(sum(c - d for _, _, d, c in rows))
