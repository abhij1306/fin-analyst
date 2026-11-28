"""`fin sql --evidence` on figures whose raw value records no sheet, cell or page (client run,
2026-11-27): the page shows the sources it has and names the result rows it has none for."""

from pathlib import Path

import pytest
from openpyxl import Workbook

from fin.audit.logger import Run
from fin.config import DEFAULT_CONFIG_TOML
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.query.run import query


@pytest.fixture
def root(data_root: Path) -> Path:
    """acme with two FY25 revenue facts from pl.xlsx: fact 1 at PL!B2, fact 2 with no position."""
    ensure_database(dictionary_db_path(data_root), "dictionary")
    (data_root / "config.toml").write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    load_seed(data_root, Run("init", {}, data_root))
    archive = data_root / "archive" / "pl.xlsx"
    archive.parent.mkdir(parents=True)
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "PL"
    sheet.append(["Particulars", "31.03.2025"])
    sheet.append(["Revenue", 100])
    book.save(archive)
    path = company_db_path(data_root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (file_name, sha256, version, fiscal_year, scope, status, "
            "archive_path, run_id, ingested_at, period_end) VALUES ('pl.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'archive/pl.xlsx', 'r', now(), DATE '2025-03-31')"
        )
        for sheet_name, cell in (("PL", "B2"), (None, None)):
            [(raw_id,)] = con.execute(
                "INSERT INTO raw_values (doc_id, sheet, cell, statement, section, raw_label, "
                "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (1, ?, ?, "
                "'profit_and_loss', '', 'Revenue', '100', '(Rs.)', 1, 'r') RETURNING raw_id",
                [sheet_name, cell],
            ).fetchall()
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, 'revenue', "
                "'FY25', DATE '2025-03-31', 'standalone', 100, true, 'r', 'FY25', 12)",
                [raw_id],
            )
    return data_root


def page(root: Path, result: dict) -> str:
    return (root / result["evidence_path"]).read_text(encoding="utf-8")


def test_rows_with_a_source_get_evidence_and_the_others_are_named(root: Path) -> None:
    sql = "SELECT fact_id, value FROM facts ORDER BY fact_id"

    result = query(Run("sql", {}, root), "acme", None, sql, evidence=True)

    assert [(r["fact_id"], r["source_ref"]) for r in result["rows"]] == [(1, "PL!B2"), (2, None)]
    assert result["rows_without_source"] == [2]
    html = page(root, result)
    assert "<td class='hit'>100</td>" in html
    assert "fact 2 (revenue FY25): its raw value &#x27;Revenue&#x27; in pl.xlsx records no " in html
    assert "Result row(s) 2 have no source: no fact_id, or a figure whose raw value" in html


def test_a_sum_with_an_unsourced_line_names_it_and_is_not_traced_in_full(root: Path) -> None:
    sql = "SELECT list(fact_id ORDER BY fact_id) AS fact_ids, sum(value) AS value FROM facts"

    result = query(Run("sql", {}, root), "acme", None, sql, evidence=True)

    [row] = result["rows"]
    assert row["source_ref"] == "sum of 2 sources: PL!B2; no source"
    assert result["rows_without_source"] == [1]
    assert "<td class='hit'>100</td>" in page(root, result)


def test_a_fully_sourced_result_names_no_rows(root: Path) -> None:
    sql = "SELECT fact_id FROM facts WHERE fact_id = 1"

    result = query(Run("sql", {}, root), "acme", None, sql, evidence=True)

    assert "rows_without_source" not in result
    assert "have no source" not in page(root, result)
