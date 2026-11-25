"""A small synthetic company for FDD F3 (docs/fdd.md): registers around the FY24 year end, two
bank_vs_invoices runs (one stale), trial balances with provision ledgers and a payroll snapshot.
Used by tests/unit/test_fdd_bsheet.py and tests/integration/test_fdd_bsheet_cli.py."""

from datetime import date
from typing import Any

from fin.db.conn import utcnow

SALES = [  # row, invoice, date, service start, amount
    (2, "INV-1", date(2024, 3, 20), date(2024, 4, 5), 100000),
    (3, "INV-2", date(2024, 4, 10), date(2024, 3, 15), 200000),
    (4, "INV-3", date(2024, 3, 25), date(2024, 3, 1), 50000),
    (5, "INV-4", date(2024, 4, 5), None, 30000),
    (6, "INV-10", date(2024, 12, 1), date(2024, 12, 1), 300000),
    (7, "INV-12", date(2024, 2, 1), date(2024, 2, 1), 500000),
    (8, "INV-13", date(2023, 6, 1), date(2023, 6, 1), 80000),
]
PURCHASES = [
    (2, "P-1", date(2024, 5, 15), date(2024, 3, 1), 150000),
    (3, "P-2", date(2024, 6, 15), date(2024, 3, 1), 90000),
    (4, "P-3", date(2024, 4, 20), date(2024, 4, 1), 70000),
]
# recon_id, item_no, kind, item amount_inr, [(side, row, date, amount)]: invoice rows are SALES rows
MATCHES = [
    (1, 1, "right_only", -100000, [("invoice", 2, date(2024, 3, 20), 100000)]),  # stale
    (1, 2, "matched", 0,
     [("invoice", 4, date(2024, 3, 25), 50000), ("bank", 1, date(2024, 3, 28), 50000)]),
    (1, 3, "difference", -50000,
     [("invoice", 7, date(2024, 2, 1), 500000), ("bank", 2, date(2024, 3, 15), 450000)]),
    (1, 4, "right_only", -80000, [("invoice", 8, date(2023, 6, 1), 80000)]),
    (2, 1, "matched", 0,
     [("invoice", 2, date(2024, 3, 20), 100000), ("bank", 3, date(2024, 5, 10), 100000)]),
    (2, 2, "right_only", -200000, [("invoice", 3, date(2024, 4, 10), 200000)]),
    (2, 3, "right_only", -300000, [("invoice", 6, date(2024, 12, 1), 300000)]),
]  # fmt: skip
EMPLOYEES = [  # code, joined, resigned, gross, ctc
    ("E1", date(2015, 4, 1), None, 1200000, 2000000),  # 9 years
    ("E2", date(2022, 1, 1), None, 900000, 1000000),  # 3 years: under the minimum
    ("E3", None, None, 500000, 600000),  # no joining date: incomplete
    ("E4", date(1990, 1, 1), None, 6000000, None),  # capped
    ("E5", date(2010, 1, 1), date(2025, 1, 31), 800000, 900000),  # resigned: left out
    ("E6", date(2020, 3, 31), None, 240000, 300000),  # exactly 5 years
]


def _file(con: Any, fid: int, kind: str, name: str, period: str, start: date, end: date) -> None:
    con.execute(
        "INSERT INTO source_files VALUES (?, 1, ?, ?, 'sha', 'a', ?, ?, ?, 'audited', 'p', "
        "'loaded', NULL, 'r0', ?)",
        [fid, kind, name, period, start, end, utcnow()],
    )


def seed(con: Any, engagement: bool = True) -> None:
    """The rows, in engagement 1 (opened here unless `engagement` is False)."""
    if engagement:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    _file(con, 1, "registers", "reg.xlsx", "FY24-FY25", date(2023, 6, 1), date(2024, 12, 1))
    _file(con, 2, "trial_balance", "tb24.xlsx", "FY24", date(2023, 4, 1), date(2024, 3, 31))
    _file(con, 3, "trial_balance", "tb25.xlsx", "FY25", date(2024, 4, 1), date(2025, 3, 31))
    _file(con, 4, "payroll", "pay.xlsx", "FY25", date(2025, 3, 31), date(2025, 3, 31))
    for kind, sheet, lines in (("sales", "Sales", SALES), ("purchase", "Purchase", PURCHASES)):
        for row, number, day, service, amount in lines:
            con.execute(
                'INSERT INTO register_lines (file_id, sheet, "row", kind, invoice_no, customer, '
                "invoice_date, service_start, amount_inr, run_id) "
                "VALUES (1, ?, ?, ?, ?, 'CUST-0001', ?, ?, ?, 'r0')",
                [sheet, row, kind, number, day, service, amount],
            )
    balances = {
        2: [("Provision for Doubtful Debts", 46000), ("Gratuity Payable", 0)],
        3: [("Provision for Doubtful Debts", 126000), ("Gratuity Payable", 500000)],
    }
    for fid, ledgers in balances.items():
        for row, (ledger, credit) in enumerate(ledgers, start=2):
            con.execute(
                "INSERT INTO trial_balance VALUES (?, 'TB', ?, ?, 'ledger', 'Provisions', NULL, ?, "
                "0, ?, 'r0')",
                [fid, row, "FY24" if fid == 2 else "FY25", ledger, credit],
            )
    for recon_id, period in ((101, "FY24"), (102, "FY25")):
        con.execute(
            "INSERT INTO recon_runs (recon_id, engagement_id, name, period, scope, left_file_id, "
            "threshold, input_hash, unmapped, run_id, created_at) VALUES "
            "(?, 1, 'bank_vs_invoices', ?, 'standalone', 9, 100000, 'h', 0, 'r0', ?)",
            [recon_id, period, utcnow()],
        )
    for recon_id, item_no, kind, inr, lines in MATCHES:
        con.execute(
            "INSERT INTO recon_items (recon_id, item_no, kind, level, key, difference, amount_inr) "
            "VALUES (?, ?, ?, 'match', 'k', ?, ?)",
            [recon_id + 100, item_no, kind, inr, inr],
        )
        for side, row, day, amount in lines:
            con.execute(
                "INSERT INTO recon_match_lines VALUES (?, ?, ?, ?, ?, ?, 'CUST-0001', ?, ?, "
                "'INR', ?, ?)",
                [recon_id + 100, item_no, side, 1 if side == "invoice" else 9,
                 "Sales" if side == "invoice" else "Bank", row, day, f"r{row}", amount, amount],
            )  # fmt: skip
    for row, (code, joined, resigned, gross, ctc) in enumerate(EMPLOYEES, start=2):
        con.execute(
            'INSERT INTO payroll_lines (file_id, sheet, "row", snapshot_date, employee_name, '
            "employee_code, joining_date, resign_date, gross_salary, ctc, run_id) "
            "VALUES (4, 'Payroll', ?, DATE '2025-03-31', ?, ?, ?, ?, ?, ?, 'r0')",
            [row, f"EMP-{code}", code, joined, resigned, gross, ctc],
        )
    con.execute("INSERT INTO payroll_units VALUES (4, 'annual', 'r0', ?)", [utcnow()])
