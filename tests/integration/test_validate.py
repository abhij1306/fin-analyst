"""Validation gates (Phase 6) on the synthetic documents: each broken file fails with its code and
loads nothing; every clean file passes every gate."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path

runner = CliRunner()


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def ingest(company: str, path: Path) -> Result:
    return runner.invoke(app, ["ingest", company, str(path)])


def documents(root: Path, company: str) -> list[str]:
    with connect(company_db_path(root, company)) as con:
        rows = con.execute("SELECT file_name FROM documents ORDER BY doc_id").fetchall()
    return [name for (name,) in rows]


def review(root: Path, company: str, file_name: str) -> dict[str, Any]:
    path = root / "review" / company / f"{file_name}.error.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_id_of(result: Result) -> str:
    match = re.search(r"run_id (\S+)$", result.stderr, re.MULTILINE)
    assert match is not None, result.stderr
    return match.group(1)


@pytest.mark.parametrize(
    ("company", "loaded_first", "broken", "code", "check", "year", "expected", "actual"),
    [
        (
            "kestrel",
            None,
            "Kestrel_UNBALANCED_balance_sheet.xlsx",
            "E401",
            "balance_sheet",
            "FY25",
            "1286361000.00",
            "1291361000.00",
        ),
        (
            "kestrel",
            None,
            "Kestrel_SUBTOTAL_mismatch.xlsx",
            "E402",
            "subtotal",
            "FY24",
            "1034787000.00",
            "1036021000.00",
        ),
        (
            "bluepeak",
            "bluepeak/Bluepeak_Polymers_FY2023-24.xlsx",
            "Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx",
            "E404",
            "cross_document",
            "FY24",
            "342100000.00",
            "377100000.00",
        ),
        (
            "tarang",
            None,
            "Tarang_FY2024-25_CASHFLOW_not_reconciling.pdf",
            "E403",
            "cash_flow",
            "FY25",
            "341619000.00",
            "342819000.00",
        ),
    ],
)
def test_broken_file_fails_with_its_code_and_loads_nothing(
    root: Path,
    samples: Path,
    audit_query: Any,
    company: str,
    loaded_first: str | None,
    broken: str,
    code: str,
    check: str,
    year: str,
    expected: str,
    actual: str,
) -> None:
    before = []
    if loaded_first is not None:
        assert ingest(company, samples / "clean" / loaded_first).exit_code == 0
        before = [Path(loaded_first).name]

    result = ingest(company, samples / "broken" / broken)

    assert result.exit_code == 1
    assert f"failed ({code}: " in result.stdout
    assert f"failed 1 ({code})" in result.stderr
    assert documents(root, company) == before
    reason = review(root, company, broken)
    assert reason["code"] == code
    first = reason["checks"][0]
    assert (first["check"], first["fiscal_year"], first["expected"], first["actual"]) == (
        check,
        year,
        expected,
        actual,
    )
    assert (root / "review" / company / broken).is_file()
    assert audit_query(
        "SELECT file_name, check_name, fiscal_year, CAST(expected AS VARCHAR), "
        "CAST(actual AS VARCHAR) FROM validation_results WHERE run_id = ? AND error_code = ? "
        "ORDER BY recorded_at LIMIT 1",
        [run_id_of(result), code],
    ) == [(broken, check, year, expected, actual)]


def test_clean_fixtures_pass_every_gate(root: Path, samples: Path, audit_query: Any) -> None:
    for company in ("kestrel", "bluepeak", "tarang"):
        result = ingest(company, samples / "clean" / company)
        assert result.exit_code == 0, result.output

    assert audit_query(
        "SELECT company, check_name, count(*) FILTER (WHERE NOT passed), count(*) > 0 "
        "FROM validation_results GROUP BY ALL ORDER BY ALL"
    ) == [
        ("bluepeak", "balance_sheet", 0, True),
        ("bluepeak", "cash_flow", 0, True),
        ("bluepeak", "cross_document", 0, True),
        ("bluepeak", "subtotal", 0, True),
        ("kestrel", "balance_sheet", 0, True),
        ("kestrel", "cash_flow", 0, True),
        ("kestrel", "subtotal", 0, True),
        ("tarang", "balance_sheet", 0, True),
        ("tarang", "cash_flow", 0, True),
        ("tarang", "cross_document", 0, True),
        ("tarang", "subtotal", 0, True),
    ]
    # Kestrel is one workbook: there is nothing to compare it with, and the log says so.
    assert audit_query(
        "SELECT file, level, message FROM events WHERE stage = 'validate' AND file LIKE 'kestrel/%'"
    ) == [
        (
            "kestrel/Kestrel_Forgings_Financials_FY23-FY25.xlsx",
            "warning",
            "cross_document and unit_sanity checks not run: no other loaded document has a "
            "confirmed figure for the same fiscal year, scope and account",
        )
    ]


def test_figures_off_by_a_unit_factor_fail_unit_sanity(
    root: Path, samples: Path, audit_query: Any
) -> None:
    clean = samples / "clean" / "bluepeak"
    assert ingest("bluepeak", clean / "Bluepeak_Polymers_FY2022-23.xlsx").exit_code == 0
    book = load_workbook(clean / "Bluepeak_Polymers_FY2023-24.xlsx")
    for sheet in book:
        assert sheet["C4"].value == "(Rs. in Crores)"
        sheet["C4"] = "(Rs. in Lakhs)"
    path = root / "Bluepeak_wrong_unit.xlsx"
    book.save(path)

    result = ingest("bluepeak", path)

    assert result.exit_code == 1
    assert "failed 1 (E405)" in result.stderr
    assert documents(root, "bluepeak") == ["Bluepeak_Polymers_FY2022-23.xlsx"]
    assert review(root, "bluepeak", path.name)["code"] == "E405"
    assert audit_query(
        "SELECT DISTINCT check_name, passed FROM validation_results "
        "WHERE run_id = ? AND file_name = ? AND NOT passed",
        [run_id_of(result), path.name],
    ) == [("unit_sanity", False)]


def test_unmapped_line_skips_its_subtotal_check_and_says_so(
    root: Path, samples: Path, audit_query: Any
) -> None:
    book = load_workbook(samples / "clean" / "bluepeak" / "Bluepeak_Polymers_FY2024-25.xlsx")
    assert book["Standalone BS"]["A23"].value.strip() == "Sundry debtors"
    book["Standalone BS"]["A23"] = "Debtors outstanding over six months"
    path = root / "edited.xlsx"
    book.save(path)

    result = ingest("bluepeak", path)

    assert result.exit_code == 0, result.output
    assert audit_query(
        "SELECT level, message FROM events WHERE run_id = ? AND stage = 'validate' "
        "AND message LIKE 'subtotal%' ORDER BY seq",
        [run_id_of(result)],
    ) == [
        (
            "warning",
            f"subtotal check not run for {year} standalone: 1 balance_sheet line(s) have no "
            "confirmed account",
        )
        for year in ("FY24", "FY25")
    ]
