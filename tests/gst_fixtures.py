"""A synthetic set for F6 GST (made-up names), written with openpyxl.

`write_gst_set` writes, in a folder: an FY25 Tally trial balance with GST, TDS, income tax and MAT
credit ledgers, the FY25 Ledger Vouchers of the tax ledgers and sales, the D4 registers workbook
(tests/recon_fixtures.py: May 2024 sales of 11,10,000 in Amount in Rs), and the FY25 GSTR-1 and
GSTR-3B summaries for April to June 2024 (the other nine months have no row). Planted:

- gstr1_vs_sales_register: May ties; June's GSTR-1 taxable value (5,00,000) has no sales register
  line (left_only, at or above the 1,00,000 threshold);
- gstr3b_vs_gstr1: June's GSTR-3B taxable value is 50,000 below GSTR-1's and its tax 9,000 below
  (below the threshold, listed);
- gstr3b_vs_books: June's output tax booked (credits on the output ledgers) is 1,50,000 more than
  GSTR-3B's (needs a query); May's set-off journal debits the output ledgers and does not count;
  ITC ties;
- rcm: May's RCM liability (18,000) ties with the RCM ledger's credit.
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from tests.recon_fixtures import PERIOD_FY25, write_registers, write_trial_balance
from tests.tally_fixtures import BANK, COMPANY, CUSTOMERS

TB = {
    "Capital Account": [("Share Capital", 0, 500000)],
    "Sundry Debtors": [(CUSTOMERS[0], 1500000, 0)],
    "Bank Accounts": [(BANK, 400000, 0)],
    "Sales Accounts": [("Software Sales", 0, 1610000)],
    "Duties & Taxes": [
        ("Output CGST", 0, 144000),
        ("Output SGST", 0, 294000),
        ("Input CGST", 0, 0),
        ("Input SGST", 0, 0),
        ("RCM Payable", 0, 0),
        ("TDS Payable", 0, 12000),
    ],
    "Provisions": [("Provision for Income Tax", 0, 90000)],
    "Loans & Advances (Asset)": [("MAT Credit Entitlement", 25000, 0)],
    "Indirect Expenses": [("Legal Fees", 725000, 0)],
}
CLASSES = {
    "Output CGST": "gst_output",
    "Output SGST": "gst_output",
    "Input CGST": "gst_input",
    "Input SGST": "gst_input",
    "RCM Payable": "rcm",
    "TDS Payable": "tds",
    "Provision for Income Tax": "income_tax",
    "MAT Credit Entitlement": "mat_credit",
}
MAY, JUN, END = datetime(2024, 5, 20), datetime(2024, 6, 20), datetime(2024, 5, 31)
# ledger -> voucher lines (date, particulars, type, number, debit, credit)
VOUCHERS = {
    "Software Sales": [(MAY, CUSTOMERS[0], "Sales", "INV-2001", 0, 1110000)],
    "Output CGST": [
        (MAY, CUSTOMERS[0], "Sales", "INV-2001", 0, 99900),
        (END, "Input CGST", "Journal", "J-1", 18000, 0),
        (JUN, CUSTOMERS[0], "Sales", "INV-3001", 0, 40500),
    ],
    "Output SGST": [
        (MAY, CUSTOMERS[0], "Sales", "INV-2001", 0, 99900),
        (END, "Input SGST", "Journal", "J-1", 18000, 0),
        (JUN, CUSTOMERS[0], "Sales", "INV-3001", 0, 190500),
    ],
    "Input CGST": [
        (MAY, "Legal Fees", "Purchase", "P-1", 9000, 0),
        (MAY, "RCM Payable", "Journal", "J-2", 9000, 0),
        (END, "Output CGST", "Journal", "J-1", 0, 18000),
    ],
    "Input SGST": [
        (MAY, "Legal Fees", "Purchase", "P-1", 9000, 0),
        (MAY, "RCM Payable", "Journal", "J-2", 9000, 0),
        (END, "Output SGST", "Journal", "J-1", 0, 18000),
    ],
    "RCM Payable": [
        (MAY, "Input CGST", "Journal", "J-2", 0, 18000),
        (datetime(2024, 6, 15), BANK, "Payment", "PAY-1", 18000, 0),
    ],
}
# month, taxable value, IGST, CGST, SGST, cess (+ GSTR-3B: ITC, RCM liability, paid cash, credit)
GSTR1 = [
    ("Apr-2024", 0, 0, 0, 0, 0),
    ("May-2024", 1110000, 0, 99900, 99900, 0),
    ("Jun-2024", 500000, 0, 45000, 45000, 0),
]
GSTR3B = [
    ("Apr-2024", 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("May-2024", 1110000, 0, 99900, 99900, 0, 36000, 18000, 181800, 36000),
    ("Jun-2024", 450000, 0, 40500, 40500, 0, 0, 0, 81000, 0),
]
GSTR1_HEADER = ["Month", "Taxable Value", "IGST", "CGST", "SGST", "Cess"]
GSTR3B_HEADER = [
    *GSTR1_HEADER, "ITC Claimed", "RCM Liability", "Paid in Cash", "Paid through Credit"
]  # fmt: skip


def write_return(
    path: Path, title: str, header: list[str], rows: list[tuple[object, ...]], period: str
) -> Path:
    """A return summary: company, title and period lines, the header, the rows and a Total."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Summary"
    for line in (COMPANY, title, f"Return period {period}"):
        sheet.append([line])
    sheet.append(header)
    for row in rows:
        sheet.append(list(row))
    sheet.append(["Total", *(sum(r[i] for r in rows) for i in range(1, len(header)))])  # type: ignore[misc]
    book.save(path)
    return path


def write_vouchers(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ledger Vouchers"
    for line in (COMPANY, "Ledger Vouchers", PERIOD_FY25):
        sheet.append([line])
    sheet.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    for ledger, lines in VOUCHERS.items():
        sheet.append([f"Ledger: {ledger}"])
        sheet.append([None, "Opening Balance", None, None, None, None])
        for when, particulars, kind, number, debit, credit in lines:
            sheet.append([when, particulars, kind, number, debit or None, credit or None])
        sheet.append([None, "Closing Balance", None, None, 1, None])
    book.save(path)
    return path


def write_gst_set(folder: Path) -> dict[str, Path]:
    """The files: tb, lv, registers, gstr1, gstr3b."""
    period = "1-Apr-2024 to 31-Mar-2025"
    return {
        "tb": write_trial_balance(folder / "tb.xlsx", TB, PERIOD_FY25),
        "lv": write_vouchers(folder / "lv.xlsx"),
        "registers": write_registers(folder / "registers.xlsx"),
        "gstr1": write_return(folder / "gstr1.xlsx", "GSTR-1 summary", GSTR1_HEADER, GSTR1, period),
        "gstr3b": write_return(
            folder / "gstr3b.xlsx", "GSTR-3B summary", GSTR3B_HEADER, GSTR3B, period
        ),
    }
