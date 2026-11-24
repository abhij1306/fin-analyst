"""Synthetic Tally-style exports (made-up names), written with openpyxl in a test's tmp_path.

- `write_trial_balance`: a Detailed trial balance: export-period header lines, a two-row column
  header (Particulars | Closing Balance Debit, Credit), bold group rows and indented ledgers, with
  parties under Sundry Debtors and Sundry Creditors. `groups_only` leaves the ledgers out (the
  Condensed export); `plant` makes Sundry Debtors' group total differ from its ledgers by 10,000.
- `write_ledger_vouchers`: Ledger Vouchers for the ledgers given, one block per ledger
  ("Ledger: <name>", then dated voucher lines between opening and closing balance rows).
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

COMPANY = "Acme Synthetic Private Limited"
CUSTOMERS = ("Orchid Systems Pvt Ltd", "Lotus Infotech LLP")
VENDORS = ("Banyan Traders", "Teak Hardware Supplies")

# (level, name, debit, credit, is_group)
TRIAL_BALANCE = [
    (0, "Capital Account", 0, 500000, True),
    (1, "Share Capital", 0, 500000, False),
    (0, "Current Liabilities", 0, 220000, True),
    (1, "Sundry Creditors", 0, 180000, True),
    (2, VENDORS[0], 0, 120000, False),
    (2, VENDORS[1], 0, 60000, False),
    (1, "Duties & Taxes", 0, 40000, True),
    (2, "GST Payable", 0, 40000, False),
    (0, "Current Assets", 550000, 0, True),
    (1, "Sundry Debtors", 300000, 0, True),
    (2, CUSTOMERS[0], 200000, 0, False),
    (2, CUSTOMERS[1], 100000, 0, False),
    (1, "Bank Accounts", 250000, 0, True),
    (2, "Axis Bank Current Account", 250000, 0, False),
    (0, "Sales Accounts", 0, 900000, True),
    (1, "Software Sales", 0, 900000, False),
    (0, "Indirect Expenses", 1070000, 0, True),
    (1, "Rent", 120000, 0, False),
    (1, "Salaries", 950000, 0, False),
]
PERIOD = "1-Apr-2024 to 31-Mar-2025"


def write_trial_balance(
    path: Path, *, period: str = PERIOD, groups_only: bool = False, plant: bool = False
) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Trial Balance"
    for line in (COMPANY, "Trial Balance", period):
        sheet.append([line])
    sheet.append(["Particulars", "Closing Balance", None])
    sheet.append([None, "Debit", "Credit"])
    sheet.merge_cells("B4:C4")
    sheet.merge_cells("A4:A5")
    for level, name, debit, credit, group in TRIAL_BALANCE:
        if groups_only and not group:
            continue
        if plant and name == "Sundry Debtors":
            debit += 10000
        sheet.append([name, debit or None, credit or None])
        cell = sheet.cell(sheet.max_row, 1)
        cell.font = Font(bold=group)
        cell.alignment = Alignment(indent=level)
    sheet.append(["Grand Total", 1620000, 1620000])
    sheet.cell(sheet.max_row, 1).font = Font(bold=True)
    book.save(path)
    return path


# ledger -> voucher lines (day, particulars, type, number, debit, credit)
BANK = "Axis Bank Current Account"
VOUCHERS = {
    CUSTOMERS[0]: [
        (12, "Software Sales", "Sales", 101, 500000, 0),
        (30, BANK, "Receipt", 7, 0, 300000),
    ],
    CUSTOMERS[1]: [
        (15, "Software Sales", "Sales", 102, 400000, 0),
        (28, BANK, "Receipt", 8, 0, 300000),
    ],
    "Software Sales": [
        (12, CUSTOMERS[0], "Sales", 101, 0, 500000),
        (15, CUSTOMERS[1], "Sales", 102, 0, 400000),
    ],
    VENDORS[0]: [(5, "Rent", "Purchase", 9, 0, 120000)],
    VENDORS[1]: [(6, "Salaries", "Journal", 10, 0, 60000)],
    "Share Capital": [(1, BANK, "Receipt", 1, 0, 500000)],
    "GST Payable": [(20, BANK, "Journal", 11, 0, 40000)],
    BANK: [(30, CUSTOMERS[0], "Receipt", 7, 300000, 0)],
    "Rent": [(5, VENDORS[0], "Purchase", 9, 120000, 0)],
    "Salaries": [(6, VENDORS[1], "Journal", 10, 950000, 0)],
    "Neem Consulting": [(9, BANK, "Payment", 12, 25000, 0)],
}


def write_ledger_vouchers(path: Path, ledgers: list[str], *, period: str = PERIOD) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ledger Vouchers"
    for line in (COMPANY, "Ledger Vouchers", period):
        sheet.append([line])
    sheet.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    for ledger in ledgers:
        sheet.append([f"Ledger: {ledger}"])
        sheet.cell(sheet.max_row, 1).font = Font(bold=True)
        sheet.append([None, "Opening Balance", None, None, None, None])
        for day, particulars, kind, number, debit, credit in VOUCHERS[ledger]:
            when = datetime(2024, 5, day)
            sheet.append([when, particulars, kind, number, debit or None, credit or None])
        sheet.append([None, "Closing Balance", None, None, 1, None])
    book.save(path)
    return path


def write_unknown_layout(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Account balances"])
    sheet.append(["Account", "Balance"])
    sheet.append([CUSTOMERS[0], 200000])
    book.save(path)
    return path
