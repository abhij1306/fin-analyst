"""A synthetic set for the D4 reconciliations (made-up names), written with openpyxl.

`write_set` writes, in a folder: the FY24 and FY25 Tally trial balances, the FY25 Ledger Vouchers,
a billing registers workbook and a payroll snapshot as of 31-Mar-2025, with these planted:

- sales (register vs the Software Sales ledger): INV-2001 matches; INV-2002 is 50,000 higher in the
  register (below the 1,00,000 threshold: listed); INV-2003 (4,00,000) is only in the register;
  INV-2004 (1,50,000) is only in the books; INV-2005 is a USD 2,000 invoice at 80 in the register
  (1,60,000) booked at 82 (1,64,000): an fx line;
- purchases (register vs the Purchases ledger): PINV-72 matches by number; PINV-71 (90,000)
  matches by party and amount (the voucher number is 9); PINV-73 (1,20,000) is only in the register;
- gl_vs_tb: the Bank ledger's trial balance change is 1,50,000 more than its Ledger Vouchers
  movement; Rent's trial balance is 20,000 more than its movement; every other ledger ties, the
  Profit & Loss A/c with the prior year's profit rolled in; Neem Consulting is only in the Ledger
  Vouchers (not mapped);
- payroll: the snapshot's CTC (70,08,000) is 1,08,000 above employee expense (69,00,000).
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from tests.payroll_fixtures import write_payroll
from tests.register_fixtures import (
    PURCHASE_HEADERS,
    SALES_HEADERS,
    purchase_line,
    sales_line,
)
from tests.tally_fixtures import BANK, COMPANY, CUSTOMERS, VENDORS

PERIOD_FY24 = "1-Apr-2023 to 31-Mar-2024"
PERIOD_FY25 = "1-Apr-2024 to 31-Mar-2025"
# group -> (ledger, debit, credit)
TB_FY24 = {
    "Capital Account": [("Share Capital", 0, 500000)],
    "Sundry Creditors": [(VENDORS[0], 0, 50000), (VENDORS[1], 0, 20000)],
    "Sundry Debtors": [(CUSTOMERS[0], 100000, 0), (CUSTOMERS[1], 50000, 0)],
    "Bank Accounts": [(BANK, 5000000, 0)],
    "Sales Accounts": [("Software Sales", 0, 8000000)],
    "Purchase Accounts": [("Purchases", 300000, 0)],
    "Indirect Expenses": [("Salaries", 6000000, 0), ("Rent", 200000, 0)],
}
TB_FY25 = {
    "Capital Account": [("Share Capital", 0, 500000)],
    "Reserves & Surplus": [("Profit & Loss A/c", 0, 1500000)],
    "Sundry Creditors": [(VENDORS[0], 0, 140000), (VENDORS[1], 0, 60000)],
    "Sundry Debtors": [(CUSTOMERS[0], 364000, 0), (CUSTOMERS[1], 400000, 0)],
    "Bank Accounts": [(BANK, 5350000, 0)],
    "Sales Accounts": [("Software Sales", 0, 814000)],
    "Purchase Accounts": [("Purchases", 130000, 0)],
    "Indirect Expenses": [("Salaries", 6900000, 0), ("Rent", 120000, 0)],
}
ACCOUNTS = {
    "Share Capital": "share_capital",
    "Profit & Loss A/c": "other_equity",
    VENDORS[0]: "trade_payables",
    VENDORS[1]: "trade_payables",
    CUSTOMERS[0]: "trade_receivables",
    CUSTOMERS[1]: "trade_receivables",
    BANK: "cash",
    "Software Sales": "revenue",
    "Purchases": "purchases_stock_in_trade",
    "Salaries": "employee_expense",
    "Rent": "other_expenses",
}

# ledger -> voucher lines (date, particulars, type, number, debit, credit)
M = datetime(2024, 5, 14)
VOUCHERS = {
    "Share Capital": [],
    "Profit & Loss A/c": [],
    VENDORS[0]: [(datetime(2024, 6, 3), "Purchases", "Purchase", 9, 0, 90000)],
    VENDORS[1]: [(datetime(2024, 6, 4), "Purchases", "Purchase", "PINV-72", 0, 40000)],
    CUSTOMERS[0]: [
        (M, "Software Sales", "Sales", "INV-2001", 300000, 0),
        (M, "Software Sales", "Sales", "INV-2005", 164000, 0),
        (datetime(2024, 5, 30), BANK, "Receipt", 7, 0, 200000),
    ],
    CUSTOMERS[1]: [
        (M, "Software Sales", "Sales", "INV-2002", 200000, 0),
        (M, "Software Sales", "Sales", "INV-2004", 150000, 0),
    ],
    BANK: [(datetime(2024, 5, 30), CUSTOMERS[0], "Receipt", 7, 200000, 0)],
    "Software Sales": [
        (M, CUSTOMERS[0], "Sales", "INV-2001", 0, 300000),
        (M, CUSTOMERS[1], "Sales", "INV-2002", 0, 200000),
        (M, CUSTOMERS[1], "Sales", "INV-2004", 0, 150000),
        (M, CUSTOMERS[0], "Sales", "INV-2005", 0, 164000),
    ],
    "Purchases": [
        (datetime(2024, 6, 3), VENDORS[0], "Purchase", 9, 90000, 0),
        (datetime(2024, 6, 4), VENDORS[1], "Purchase", "PINV-72", 40000, 0),
    ],
    "Salaries": [
        (datetime(2024, 9, 30), BANK, "Journal", 21, 3450000, 0),
        (datetime(2025, 3, 31), BANK, "Journal", 22, 3450000, 0),
    ],
    "Rent": [(datetime(2024, 6, 1), BANK, "Payment", 23, 100000, 0)],
    "Neem Consulting": [(datetime(2024, 5, 9), BANK, "Payment", 12, 25000, 0)],
}


def write_trial_balance(
    path: Path, groups: dict[str, list[tuple[str, int, int]]], period: str
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
    total = [0, 0]
    for group, ledgers in groups.items():
        debit, credit = sum(x[1] for x in ledgers), sum(x[2] for x in ledgers)
        total[0] += debit
        total[1] += credit
        sheet.append([group, debit or None, credit or None])
        sheet.cell(sheet.max_row, 1).font = Font(bold=True)
        for name, d, c in ledgers:
            sheet.append([name, d or None, c or None])
            sheet.cell(sheet.max_row, 1).alignment = Alignment(indent=1)
    sheet.append(["Grand Total", *total])
    book.save(path)
    return path


def write_vouchers(path: Path, ledgers: list[str] | None = None) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ledger Vouchers"
    for line in (COMPANY, "Ledger Vouchers", PERIOD_FY25):
        sheet.append([line])
    sheet.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    for ledger in ledgers or list(VOUCHERS):
        sheet.append([f"Ledger: {ledger}"])
        sheet.append([None, "Opening Balance", None, None, None, None])
        for when, particulars, kind, number, debit, credit in VOUCHERS[ledger]:
            sheet.append([when, particulars, kind, number, debit or None, credit or None])
        sheet.append([None, "Closing Balance", None, None, 1, None])
    book.save(path)
    return path


def write_registers(path: Path) -> Path:
    def sale(n: int, customer: str, amount: float, number: str, **over: object) -> list[object]:
        return sales_line(n, customer, amount, **{"Invoice No": number, **over})

    book = Workbook()
    first = book.active
    assert first is not None
    first.title = "Sales Register"
    first.append(SALES_HEADERS)
    for line in (
        sale(1, CUSTOMERS[0], 300000, "INV-2001"),
        sale(2, CUSTOMERS[1], 250000, "INV-2002"),
        sale(3, CUSTOMERS[0], 400000, "INV-2003"),
        sale(5, CUSTOMERS[0], 160000, "INV-2005", usd=True),
    ):
        first.append(line)
    sheet = book.create_sheet("Purchase Register")
    sheet.append(PURCHASE_HEADERS)
    for line in (
        purchase_line(1, VENDORS[0], 90000, "PINV-71"),
        purchase_line(2, VENDORS[1], 40000, "PINV-72"),
        purchase_line(3, VENDORS[0], 120000, "PINV-73"),
    ):
        sheet.append(line)
    book.save(path)
    return path


def write_set(folder: Path) -> dict[str, Path]:
    """The files of the set: tb24, tb25, lv, registers, payroll."""
    return {
        "tb24": write_trial_balance(folder / "tb24.xlsx", TB_FY24, PERIOD_FY24),
        "tb25": write_trial_balance(folder / "tb25.xlsx", TB_FY25, PERIOD_FY25),
        "lv": write_vouchers(folder / "lv.xlsx"),
        "registers": write_registers(folder / "registers.xlsx"),
        "payroll": write_payroll(folder / "payroll.xlsx"),
    }
