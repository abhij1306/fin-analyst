"""A synthetic set for FDD F5 (made-up names): the D4 recon set (tests/recon_fixtures.py) with a
director's loan ledger and its interest, and a disclosure reading CSV of the notes to accounts.

Planted:

- Orchid Systems Pvt Ltd (a customer, CUST-0001) is a related party; the notes disclose sales of
  10 lakh, the sales register has 8,60,000 for it: a difference of 1,40,000 (at or above the
  1,00,000 threshold);
- Ravi Menon (a director, PRTY-0001: in no loaded file) took a loan: the notes disclose 1.5 lakh
  taken; the Loan from Director ledger has 2,10,000 of credits in FY25 (50,000 interest, 1,50,000
  received and a 10,000 accrual reversed by a 10,000 debit): a difference of -60,000 (below the
  threshold); disclosed sales of 1 lakh to him have no
  customer linked until a link to CUST-0002 (2,50,000 of sales) is confirmed;
- the loan: 4,00,000 owed at the end of FY24, 6,00,000 at the end of FY25, 50,000 interest net of
  the reversal: an implied rate of 10% on the 5,00,000 average; FY24 has no Ledger Vouchers, so its
  interest is not known (NULL);
- a tax demand (AY 2021-22, 12.5 lakh, 2.5 lakh paid under protest) and a guarantee given to Orchid
  (its name masked in the nature).
"""

import csv
from datetime import datetime
from pathlib import Path

from tests import recon_fixtures as rf
from tests.tally_fixtures import BANK, CUSTOMERS

LOAN, INTEREST = "Loan from Director", "Interest on Loan"
TB_FY24 = {**rf.TB_FY24, "Unsecured Loans": [(LOAN, 0, 400000)]}
TB_FY25 = {
    **rf.TB_FY25,
    "Unsecured Loans": [(LOAN, 0, 600000)],
    "Indirect Expenses": [*rf.TB_FY25["Indirect Expenses"], (INTEREST, 50000, 0)],
}
ACCOUNTS = {**rf.ACCOUNTS, LOAN: "lt_borrowings", INTEREST: "finance_cost"}
J = datetime(2025, 3, 31)
VOUCHERS = {
    **rf.VOUCHERS,
    LOAN: [
        (J, INTEREST, "Journal", 31, 0, 50000),
        (J, BANK, "Receipt", 32, 0, 150000),
        (J, INTEREST, "Journal", 33, 0, 10000),  # an accrual reversed by voucher 34
        (J, INTEREST, "Journal", 34, 10000, 0),
    ],
    INTEREST: [(J, LOAN, "Journal", 31, 50000, 0), (J, LOAN, "Journal", 33, 10000, 0)]
    + [(J, LOAN, "Journal", 34, 0, 10000)],
}
COLUMNS = [
    "kind", "period", "counterparty", "nature", "amount", "unit", "relationship", "books",
    "ledger", "forum", "stage", "assessment_year", "demand", "paid_under_protest", "page",
]  # fmt: skip


def row(kind: str, nature: str, page: int, period: str = "FY25", **fields: str) -> list[str]:
    """A reading row in COLUMNS order; the fields not given are empty."""
    values = {"kind": kind, "period": period, "nature": nature, "page": str(page), **fields}
    return [values.get(column, "") for column in COLUMNS]


RAVI = "Ravi Menon"
ROWS = [
    row("related_party", "Enterprise with a common director", 12, counterparty=CUSTOMERS[0],
        relationship="Common director"),
    row("related_party", "Director", 12, counterparty=RAVI,
        relationship="Key managerial personnel"),
    row("related_party_transaction", "Sale of services", 13, counterparty=CUSTOMERS[0], amount="10",
        unit="lakh", books="sales"),
    row("related_party_transaction", "Loan taken", 13, counterparty=RAVI, amount="1.5", unit="lakh",
        books="credit", ledger=LOAN),
    row("related_party_transaction", "Sale of services", 13, counterparty=RAVI, amount="1",
        unit="lakh", books="sales"),
    row("related_party_balance", "Loan payable", 13, counterparty=RAVI, amount="6", unit="lakh"),
    row("tax_matter", "Income tax demand under appeal", 14, unit="lakh", forum="CIT(A)",
        stage="Appeal filed", assessment_year="AY 2021-22", demand="12.5",
        paid_under_protest="2.5"),
    row("guarantee", f"Bank guarantee given for {CUSTOMERS[0]}", 15, amount="20", unit="lakh"),
]  # fmt: skip


def write_vouchers(path: Path) -> Path:
    """The FY25 Ledger Vouchers of the recon set plus the loan and interest ledgers."""
    saved = dict(rf.VOUCHERS)
    rf.VOUCHERS.update(VOUCHERS)
    try:
        return rf.write_vouchers(path, list(VOUCHERS))
    finally:
        rf.VOUCHERS.clear()
        rf.VOUCHERS.update(saved)


def write_reading(path: Path, rows: list[list[str]] | None = None) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(ROWS if rows is None else rows)
    return path


def write_set(folder: Path) -> dict[str, Path]:
    """tb24, tb25, lv, registers, notes (a stand-in document) and reading."""
    notes = folder / "notes.pdf"
    notes.write_bytes(b"%PDF-1.4 synthetic notes to accounts")
    return {
        "tb24": rf.write_trial_balance(folder / "tb24.xlsx", TB_FY24, rf.PERIOD_FY24),
        "tb25": rf.write_trial_balance(folder / "tb25.xlsx", TB_FY25, rf.PERIOD_FY25),
        "lv": write_vouchers(folder / "lv.xlsx"),
        "registers": rf.write_registers(folder / "registers.xlsx"),
        "notes": notes,
        "reading": write_reading(folder / "reading.csv"),
    }
