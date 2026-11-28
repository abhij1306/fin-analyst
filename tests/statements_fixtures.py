"""A synthetic company for the statements in the display unit (SPEC-06; made-up figures).

Company acme, standalone, fiscal years FY24 and FY25, each from its own workbook. In INR Mn to one
decimal FY25's balance sheet does not add up after rounding: property 0.55, receivables 0.55 and
cash 0.54 show as 0.6, 0.6 and 0.5 (1.7), the printed total assets 1.64 as 1.6.
"""

from pathlib import Path

from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path

# (statement, account, FY24 value, FY25 value), in rupees (earnings per share: rupees per share)
FIGURES = [
    ("profit_and_loss", "revenue", 2000000, 2540000),
    ("profit_and_loss", "employee_expense", 1200000, 1460000),
    ("profit_and_loss", "depreciation", 100000, 120000),
    ("profit_and_loss", "other_expenses", 450000, 480000),
    ("profit_and_loss", "pat", 250000, 480000),
    ("profit_and_loss", "eps_basic", "4.17", "8.00"),
    ("balance_sheet", "ppe_net", 500000, 550000),
    ("balance_sheet", "trade_receivables", 400000, 550000),
    ("balance_sheet", "cash", 300000, 540000),
    ("balance_sheet", "total_assets", 1200000, 1640000),
]
YEARS = {"FY24": "2024-03-31", "FY25": "2025-03-31"}


def make_statements_root(base: Path) -> Path:
    """The data folder with the dictionary and acme's two years of statements, all confirmed."""
    root = base / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("test", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    raw_id = 0
    with connect(path, write=True) as con:
        for doc_id, (year, end) in enumerate(YEARS.items(), start=1):
            con.execute(
                "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
                "status, archive_path, run_id, ingested_at) VALUES (?, ?, ?, 1, ?, 'standalone', "
                "'loaded', 'x', 'r0', now())",
                [doc_id, f"fs_{year}.xlsx", year, year],
            )
            for statement, account, *values in FIGURES:
                raw_id += 1
                con.execute(
                    "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                    "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                    "VALUES (?, ?, 'FS', ?, ?, '', ?, '', '', 1, 'r0')",
                    [raw_id, doc_id, f"C{raw_id}", statement, account],
                )
                con.execute(
                    "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                    "value, confirmed, run_id, period, period_months) "
                    "VALUES (?, ?, ?, ?, CAST(? AS DATE), 'standalone', ?, true, 'r0', ?, 12)",
                    [raw_id, doc_id, account, year, end, str(values[doc_id - 1]), year],
                )
    return root
