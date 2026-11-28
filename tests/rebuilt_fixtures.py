"""A synthetic diligence company for the rebuilt statements (SPEC-14; made-up names).

Company acme, an engagement for FY24 and FY25, and balanced Tally trial balances for both years,
every ledger confirmed to an account (`ACCOUNTS`). FY24 is the first year (no cash flow); FY25's
derived cash flow: operating 2,90,000, investing -2,20,000, financing 0, cash 2,50,000 to
3,20,000. The audited FY25 standalone statement (`REPORTED`) equals the rebuilt figures except
the planted differences in `PLANTED` (reported - rebuilt).
"""

from pathlib import Path

from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import ledgers
from fin.diligence.sources import ingest_source
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from tests.recon_fixtures import write_trial_balance

PERIODS = {"FY24": "1-Apr-2023 to 31-Mar-2024", "FY25": "1-Apr-2024 to 31-Mar-2025"}
# group -> (ledger, debit, credit)
TRIAL_BALANCES = {
    "FY24": {
        "Capital Account": [("Share Capital", 0, 500000)],
        "Reserves & Surplus": [("Profit & Loss A/c", 0, 200000)],
        "Loans (Liability)": [("Term Loan", 0, 300000)],
        "Sundry Creditors": [("Banyan Traders", 0, 150000)],
        "Duties & Taxes": [("GST Payable", 0, 50000)],
        "Fixed Assets": [("Plant and Machinery", 600000, 0)],
        "Sundry Debtors": [("Orchid Systems Pvt Ltd", 400000, 0)],
        "Bank Accounts": [("Axis Bank Current Account", 250000, 0)],
        "Sales Accounts": [("Software Sales", 0, 2000000)],
        "Indirect Expenses": [("Salaries", 1500000, 0), ("Depreciation", 100000, 0)]
        + [("Rent", 350000, 0)],
    },
    "FY25": {
        "Capital Account": [("Share Capital", 0, 600000)],
        "Reserves & Surplus": [("Profit & Loss A/c", 0, 250000)],
        "Loans (Liability)": [("Term Loan", 0, 200000)],
        "Sundry Creditors": [("Banyan Traders", 0, 180000)],
        "Duties & Taxes": [("GST Payable", 0, 70000)],
        "Fixed Assets": [("Plant and Machinery", 700000, 0)],
        "Sundry Debtors": [("Orchid Systems Pvt Ltd", 500000, 0)],
        "Bank Accounts": [("Axis Bank Current Account", 320000, 0)],
        "Sales Accounts": [("Software Sales", 0, 2400000)],
        "Indirect Expenses": [("Salaries", 1700000, 0), ("Depreciation", 120000, 0)]
        + [("Rent", 360000, 0)],
    },
}
ACCOUNTS = {
    "Share Capital": "share_capital",
    "Profit & Loss A/c": "other_equity",
    "Term Loan": "lt_borrowings",
    "Banyan Traders": "trade_payables",
    "GST Payable": "other_current_liabilities",
    "Plant and Machinery": "ppe_net",
    "Orchid Systems Pvt Ltd": "trade_receivables",
    "Axis Bank Current Account": "cash",
    "Software Sales": "revenue",
    "Salaries": "employee_expense",
    "Depreciation": "depreciation",
    "Rent": "other_expenses",
}
# The audited FY25 standalone statement: (statement, account, value). Revenue and trade receivables
# are planted 1,00,000 above the books, so total assets and profit for the year differ too.
REPORTED = [
    ("balance_sheet", "share_capital", 600000),
    ("balance_sheet", "other_equity", 470000),
    ("balance_sheet", "lt_borrowings", 200000),
    ("balance_sheet", "trade_payables", 180000),
    ("balance_sheet", "other_current_liabilities", 70000),
    ("balance_sheet", "ppe_net", 700000),
    ("balance_sheet", "trade_receivables", 600000),
    ("balance_sheet", "cash", 320000),
    ("balance_sheet", "total_assets", 1620000),
    ("profit_and_loss", "revenue", 2500000),
    ("profit_and_loss", "employee_expense", 1700000),
    ("profit_and_loss", "depreciation", 120000),
    ("profit_and_loss", "other_expenses", 360000),
    ("profit_and_loss", "pat", 320000),
    ("cash_flow", "cfo", 290000),
]
PLANTED = {"revenue": 100000, "trade_receivables": 100000, "total_assets": 100000, "pat": 100000}


def run(root: Path) -> Run:
    return Run("test", {}, root)


def _reported(con: object) -> None:
    con.execute(  # type: ignore[attr-defined]
        "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, status, "
        "archive_path, run_id, ingested_at) VALUES (1, 'fs.xlsx', 'x', 1, 'FY25', 'standalone', "
        "'loaded', 'x', 'r0', now())"
    )
    for raw_id, (statement, account, value) in enumerate(REPORTED, start=1):
        con.execute(  # type: ignore[attr-defined]
            "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, raw_label, "
            "raw_value_text, raw_unit, rupees_per_unit, run_id) "
            "VALUES (?, 1, 'FS', ?, ?, '', ?, '', '', 1, 'r0')",
            [raw_id, f"C{raw_id}", statement, account],
        )
        con.execute(  # type: ignore[attr-defined]
            "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, value, "
            "confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
            "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
            [raw_id, account, value],
        )


def make_root(base: Path, periods: tuple[str, ...] = ("FY24", "FY25"), mapped: bool = True) -> Path:
    """The data folder: dictionary, company acme with its FY25 statement, the engagement, the
    trial balances of `periods` loaded and (when `mapped`) every ledger confirmed."""
    root = base / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, run(root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        _reported(con)
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    for period in periods:
        tb = write_trial_balance(
            base / f"tb_{period}.xlsx", TRIAL_BALANCES[period], PERIODS[period]
        )
        ingest_source(run(root), load_config(root), "acme", tb, "trial_balance", None)
    if mapped and periods:
        map_all(root)
    return root


def map_all(root: Path) -> None:
    proposals = [
        {"ledger": ledger, "account": account, "confidence": 0.9, "reason": "by name"}
        for ledger, account in ACCOUNTS.items()
    ]
    ledgers.propose(run(root), "acme", proposals)
    ids = [row["ledger_id"] for row in ledgers.list_ledgers(run(root), "acme")]
    ledgers.confirm(run(root), "acme", ids)
