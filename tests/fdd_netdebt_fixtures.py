"""A small synthetic company for FDD F4 (docs/fdd.md): two trial balances (FY24, FY25) with cash,
debt, debt-like, profit and loss and unmapped ledgers, their account mappings, and a stub with no
trial balance. Used by tests/unit/test_fdd_netdebt.py and
tests/integration/test_fdd_netdebt_cli.py."""

from datetime import date
from typing import Any

from fin.db.conn import utcnow

# ledger, confirmed account (None: not mapped), FY24 and FY25 (debit, credit) (None: not in that TB)
LEDGERS = [
    ("HDFC Bank", "cash_and_bank_balances", (500000, 0), (0, 20000)),  # overdrawn in FY25
    ("Cash in hand", "cash", None, (10000, 0)),
    ("Term Loan", "lt_borrowings", (0, 1000000), (0, 800000)),
    ("Lease Liability", "lease_liabilities_current", (0, 50000), (0, 50000)),
    ("Gratuity Provision", "lt_provisions", (0, 100000), (0, 120000)),
    ("TDS Payable", "other_current_liabilities", None, (0, 30000)),
    ("Acme Traders", "trade_receivables", None, (400000, 0)),  # a customer's ledger, by name
    ("Sales", "revenue", (0, 4000000), (0, 5000000)),
    ("Suspense", None, None, (5000, 0)),
]
STATEMENT = {"revenue": "profit_and_loss"}


def seed(con: Any, engagement: bool = True) -> None:
    """The rows, in engagement 1 (opened here, with the stub FY26-YTD-SEP, unless `engagement` is
    False)."""
    now = utcnow()
    if engagement:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, status, "
            "opened_run_id, opened_at) VALUES ('FY24,FY25', 100000, 'FY26-YTD-SEP', "
            "DATE '2025-04-01', DATE '2025-09-30', 'open', 'r0', ?)",
            [now],
        )
    for fid, period, start, end in (
        (2, "FY24", date(2023, 4, 1), date(2024, 3, 31)),
        (3, "FY25", date(2024, 4, 1), date(2025, 3, 31)),
    ):
        con.execute(
            "INSERT INTO source_files VALUES (?, 1, 'trial_balance', ?, 'sha', 'a', ?, ?, ?, "
            "'audited', 'p', 'loaded', NULL, 'r0', ?)",
            [fid, f"tb{period[2:]}.xlsx", period, start, end, now],
        )
    for row, (ledger, account, fy24, fy25) in enumerate(LEDGERS, start=2):
        for fid, period, amounts in ((2, "FY24", fy24), (3, "FY25", fy25)):
            if amounts is not None:
                con.execute(
                    "INSERT INTO trial_balance VALUES "
                    "(?, 'TB', ?, ?, 'ledger', 'G', NULL, ?, ?, ?, 'r0')",
                    [fid, row, period, ledger, *amounts],
                )
        if account is not None:
            statement = STATEMENT.get(account, "balance_sheet")
            con.execute(
                "INSERT INTO ledger_accounts (ledger, group_name, status, account, statement, "
                "side, run_id, changed_at) VALUES (?, 'G', 'confirmed', ?, ?, 1, 'r0', ?)",
                [ledger, account, statement, now],
            )
    con.execute(
        "INSERT INTO mask_values VALUES ('CUST-0001', 'customer', 'Acme Traders', 'acme traders', "
        "1, 'r0')"
    )
