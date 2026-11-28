"""A synthetic data folder for the question tests: the seeded dictionary and company acme with one
FY25 standalone workbook (fy25.xlsx) of revenue, cfo, trade receivables and cost of materials."""

import shutil
from pathlib import Path

import pytest

from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path

LINES = [  # account, printed label, cell, value in rupees
    ("revenue", "Revenue from operations", "B2", "1000.00"),
    ("cfo", "Net cash from operating activities", "B3", "250.00"),
    ("trade_receivables", "Trade receivables", "B4", "200.00"),
    ("cost_of_materials", "Cost of materials consumed", "B5", "600.00"),
]


def make_template(factory: pytest.TempPathFactory) -> Path:
    """The data folder with acme, made once per test module."""
    return make_root(factory.mktemp("questions") / "FinAnalysis", "acme")


def copy_root(template: Path, data_root: Path) -> Path:
    """A copy of `template` at the test's data folder."""
    shutil.copytree(template, data_root)
    return data_root


def make_root(root: Path, *companies: str) -> Path:
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    for company in companies:
        add_company(root, company)
    return root


def add_company(root: Path, company: str) -> Path:
    path = company_db_path(root, company)
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (file_name, sha256, version, fiscal_year, scope, status, "
            "archive_path, run_id, ingested_at, period_end) VALUES ('fy25.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'archive/fy25.xlsx', 'r', now(), DATE '2025-03-31')"
        )
        for account, label, cell, value in LINES:
            [(raw_id,)] = con.execute(
                "INSERT INTO raw_values (doc_id, sheet, cell, statement, section, raw_label, "
                "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (1, 'FS', ?, "
                "'profit_and_loss', '', ?, ?, '(Rs.)', 1, 'r') RETURNING raw_id",
                [cell, label, value],
            ).fetchall()
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r', 'FY25', 12)",
                [raw_id, account, value],
            )
    return path
