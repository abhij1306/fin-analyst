"""A synthetic company for the diligence screens (SPEC-09, made-up figures): acme with one loaded
FY25 document whose figures are mapped and confirmed, no engagement yet, and walk sheets.

Planted figures (rupees): revenue 90,00,000, other income 50,000, total income 90,50,000, employee
expense 30,00,000, other expenses 45,00,000, finance cost 60,000, depreciation 1,90,000, total
expenses 77,50,000, profit before tax 13,00,000, so EBITDA 15,00,000. The walk sheet adds back
1,00,000 of legal fees (cost effect -1,00,000) and strips 2,00,000 of revenue."""

import csv
from pathlib import Path

from openpyxl import Workbook

from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path

FIGURES = {
    "revenue": 9000000, "other_income": 50000, "total_income": 9050000,
    "employee_expense": 3000000, "other_expenses": 4500000, "finance_cost": 60000,
    "depreciation": 190000, "total_expenses": 7750000, "pbt": 1300000,
    "trade_receivables": 400000, "cfo": 850000,
}  # fmt: skip
STATEMENTS = {"trade_receivables": "balance_sheet", "cfo": "cash_flow"}
HEADER = [
    "Period", "Scope", "Category", "Account", "Revenue effect", "Cost effect", "Description",
    "Source reference",
]  # fmt: skip
ROWS = [
    ["FY25", "standalone", "normalisation", "other_expenses", None, -100000,
     "One-off legal fees", "Notes p. 12"],
    ["FY25", "standalone", "management", "revenue", -200000, None,
     "Revenue booked ahead of delivery", None],
]  # fmt: skip


def build_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at) VALUES (1, 'afs-FY25.xlsx', 'sha-1', 1, "
            "'FY25', 'standalone', 'loaded', 'x', 'r0', now())"
        )
        for raw_id, (account, value) in enumerate(FIGURES.items(), start=1):
            statement = STATEMENTS.get(account, "profit_and_loss")
            con.execute(
                "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                "VALUES (?, 1, 'PL', ?, ?, '', ?, '', '', 1, 'r0')",
                [raw_id, f"C{raw_id}", statement, account],
            )
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
                [raw_id, account, value],
            )
    return root


def write_walk(path: Path, rows: list[list[object]] | None = None, sheet: str = "Walk") -> Path:
    """The walk sheet as .xlsx (header on row 1, the rows below), or .csv by the suffix."""
    body = [HEADER, *(ROWS if rows is None else rows)]
    if path.suffix == ".csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(["" if c is None else c for c in r] for r in body)
        return path
    book = Workbook()
    page = book.active
    assert page is not None
    page.title = sheet
    for row in body:
        page.append(row)
    book.save(path)
    return path
