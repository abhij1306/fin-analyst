"""A synthetic engagement for the D7 QoE outputs (made-up names): the D4 set (FY24 and FY25 trial
balances, FY25 Ledger Vouchers, registers) with the ledgers mapped, audited FY24 and FY25 statement
figures, the stub FY26-YTD-SEP (its trial balance and Ledger Vouchers), gl_vs_tb run, and one
accepted and one proposed adjustment.

Planted so the figures are known (rupees):
- working capital: receivables 1,50,000 -> 3,64,000 + 4,00,000, payables 70,000 -> 2,00,000
  (net 80,000 at FY24, 5,64,000 at FY25); monthly: April 80,000, May 6,94,000, June to March
  5,64,000;
- the stub's trial balance: receivables 9,00,000, payables 2,50,000 (net 6,50,000); its Ledger
  Vouchers move the receivables ledger CUST-0001 by 50,000 only, so that ledger ties and the rest
  do not (excluded from the monthly view);
- EBITDA FY24 = 12,00,000 and FY25 = 15,00,000 (revenue 80,00,000 and 90,00,000); one accepted
  adjustment of 1,00,000 (FY25, a cost add-back) and one proposed of 40,000;
- CUST-0001 is the pass-through customer: FY25 sales register revenue 11,10,000 is 8,60,000
  pass-through (one line with a Purchase Id, 90,000 of OEM purchase behind it) and 2,50,000 core;
- one approved accountant query on the stub's ledger differences.
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import ledgers, passthrough
from fin.diligence.sources import ingest_source
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.reconcile import run_new
from tests.recon_fixtures import (
    ACCOUNTS,
    write_set,
    write_trial_balance,
)
from tests.tally_fixtures import BANK, COMPANY, CUSTOMERS, VENDORS

STUB_PERIOD = "1-Apr-2025 to 30-Sep-2025"
TB_STUB = {
    "Capital Account": [("Share Capital", 0, 500000)],
    "Sundry Creditors": [(VENDORS[0], 0, 150000), (VENDORS[1], 0, 100000)],
    "Sundry Debtors": [(CUSTOMERS[0], 414000, 0), (CUSTOMERS[1], 486000, 0)],
    "Bank Accounts": [(BANK, 5350000, 0)],
}
# Per period: account -> value, as the audited statements print them (income positive).
FIGURES = {
    "FY24": {
        "revenue": 8000000, "pbt": 1000000, "finance_cost": 50000,
        "depreciation": 150000, "other_income": 0, "cf_op_profit_before_wc": 1100000,
        "cf_cash_generated": 900000, "cf_taxes_paid": -200000, "cfo": 700000, "cf_capex": -100000,
    },
    "FY25": {
        "revenue": 9000000, "pbt": 1300000, "finance_cost": 60000,
        "depreciation": 190000, "other_income": 50000, "cf_op_profit_before_wc": 1450000,
        "cf_cash_generated": 1100000, "cf_taxes_paid": -250000, "cfo": 850000,
        "cf_capex": -150000,
    },
}  # fmt: skip
STATEMENTS = {"revenue": "profit_and_loss", "cfo": "cash_flow"}
LV_STUB_DATE = datetime(2025, 5, 20)


def run(root: Path) -> Run:
    return Run("test", {}, root)


def write_stub_vouchers(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ledger Vouchers"
    for line in (COMPANY, "Ledger Vouchers", STUB_PERIOD):
        sheet.append([line])
    sheet.append(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    for ledger, debit in ((CUSTOMERS[0], 50000), (CUSTOMERS[1], 20000), (VENDORS[0], 0)):
        sheet.append([f"Ledger: {ledger}"])
        sheet.append([None, "Opening Balance", None, None, None, None])
        if debit:
            sheet.append([LV_STUB_DATE, "Software Sales", "Sales", "INV-3001", debit, None])
        sheet.append([None, "Closing Balance", None, None, 1, None])
    book.save(path)
    return path


def add_figures(root: Path) -> None:
    """Audited statement figures of FY24 and FY25 (one document each)."""
    with connect(company_db_path(root, "acme"), write=True) as con:
        raw_id = 0
        for doc_id, (period, accounts) in enumerate(FIGURES.items(), start=1):
            con.execute(
                "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
                "status, archive_path, run_id, ingested_at) VALUES (?, ?, ?, 1, ?, 'standalone', "
                "'loaded', 'x', 'r0', now())",
                [doc_id, f"afs-{period}.xlsx", period, period],
            )
            end = "2024-03-31" if period == "FY24" else "2025-03-31"
            for account, value in accounts.items():
                raw_id += 1
                statement = STATEMENTS.get(account, "profit_and_loss")
                if account.startswith("cf_"):
                    statement = "cash_flow"
                con.execute(
                    "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                    "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                    "VALUES (?, ?, 'PL', ?, ?, '', ?, '', '', 1, 'r0')",
                    [raw_id, doc_id, f"C{raw_id}", statement, account],
                )
                con.execute(
                    "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                    "value, confirmed, run_id, period, period_months) VALUES (?, ?, ?, ?, ?, "
                    "'standalone', ?, true, 'r0', ?, 12)",
                    [raw_id, doc_id, account, period, end, value, period],
                )


def _add_adjustments_and_query(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        for amount, status in ((100000, "accepted"), (40000, "proposed")):
            con.execute(
                "INSERT INTO adjustments (engagement_id, period, scope, category, amount, "
                "revenue_effect, cost_effect, description, source_ref, status, created_run_id, "
                "created_at, status_run_id, status_at) VALUES (1, 'FY25', 'standalone', "
                "'normalisation', ?, 0, ?, 'One-off legal fees', 'Notes p. 12', ?, 'r', now(), "
                "'r', now())",
                [amount, -amount, status],
            )
        con.execute(
            "INSERT INTO accountant_queries (engagement_id, origin, period, scope, question, "
            "amount, sources, status, created_run_id, created_at, status_run_id, status_at) "
            "VALUES (1, 'manual', 'FY26-YTD-SEP', 'standalone', 'What explains the movement on "
            "the receivable ledgers?', 150000, 'lv-stub.xlsx Ledger Vouchers', 'approved', 'r', "
            "now(), 'r', now())"
        )


def build_root(tmp_path: Path, template: Path | None = None) -> Path:
    """The engagement described in the module docstring, ready to report on."""
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "acme"), "company")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, "
            "status, opened_run_id, opened_at) VALUES ('FY24,FY25', 100000, 'FY26-YTD-SEP', "
            "DATE '2025-04-01', DATE '2025-09-30', 'open', 'r0', ?)",
            [utcnow()],
        )
    add_figures(root)
    files = write_set(tmp_path)
    stub_tb = write_trial_balance(tmp_path / "tb-stub.xlsx", TB_STUB, STUB_PERIOD)
    stub_lv = write_stub_vouchers(tmp_path / "lv-stub.xlsx")
    for path, kind in (
        (files["tb24"], "trial_balance"),
        (files["tb25"], "trial_balance"),
        (stub_tb, "trial_balance"),
        (files["lv"], "ledger_vouchers"),
        (stub_lv, "ledger_vouchers"),
        (files["registers"], "registers"),
    ):
        ingest_source(run(root), load_config(root), "acme", path, kind, None)
    proposals = [
        {"ledger": k, "account": v, "confidence": 0.9, "reason": "by name"}
        for k, v in ACCOUNTS.items()
    ]
    ledgers.propose(run(root), "acme", proposals)
    ids = [row["ledger_id"] for row in ledgers.list_ledgers(run(root), "acme")]
    ledgers.confirm(run(root), "acme", ids)
    passthrough.set_customers(run(root), "acme", ["CUST-0001"], "the user's list")
    _add_adjustments_and_query(root)
    run_new(run(root), "acme", "gl_vs_tb", "FY25")
    run_new(run(root), "acme", "gl_vs_tb", "FY26-YTD-SEP")
    return root
