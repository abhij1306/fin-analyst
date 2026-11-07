"""Claude readings: `fin ingest --reading` for what fin cannot read, and `fin verify`."""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import analytics_db_path, company_db_path
from tests.synthetic_pdf import Page, write_pdf

runner = CliRunner()
HEADER = "statement,scope,period_end,period_months,unit,section,label,value_as_printed,source"
UNIT = "(Rs. in Lakhs)"

# (statement, period_end, period_months, section, label, value, page)
BALANCE_SHEET = [
    ("balance_sheet", "2025-03-31", "", "Current assets", "Cash and cash equivalents", "28.50", 1),
    ("balance_sheet", "2025-03-31", "", "Current assets", "Security deposits", "71.50", 1),
    ("balance_sheet", "2025-03-31", "", "", "TOTAL ASSETS", "100.00", 1),
    ("balance_sheet", "2025-03-31", "", "", "TOTAL EQUITY AND LIABILITIES", "100.00", 1),
]
PROFIT_AND_LOSS = [
    ("profit_and_loss", "2025-03-31", "12", "", "Revenue from operations", "500.00", 2),
    ("profit_and_loss", "2025-03-31", "12", "", "Other income", "20.00", 2),
    ("profit_and_loss", "2025-03-31", "12", "", "Total income", "520.00", 2),
]


def write_reading(path: Path, lines: list[tuple[str, ...]]) -> Path:
    rows = [HEADER] + [
        f'{statement},standalone,{end},{months},{UNIT},{section},{label},"{value}",page {page}'
        for statement, end, months, section, label, value, page in lines
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def mixed_report(path: Path) -> Path:
    """Page 1: a balance sheet fin reads. Page 2: a P&L with no unit line, which fin skips. Both
    print FY24 (the same figures) next to FY25; the readings hold FY25 only."""
    header = [(380, 85, "31 March 2025"), (470, 85, "31 March 2024")]
    balance_sheet: Page = [
        (50, 50, "STANDALONE BALANCE SHEET"),
        (50, 65, UNIT),
        *header,
        (50, 105, "Current assets"),
    ]
    for n, (*_, label, value, _page) in enumerate(BALANCE_SHEET):
        y = 120 + 15 * n
        balance_sheet += [(50, y, label), (390, y, value), (480, y, value)]
    profit_and_loss: Page = [(50, 50, "STANDALONE STATEMENT OF PROFIT AND LOSS"), *header]
    for n, (*_, label, value, _page) in enumerate(PROFIT_AND_LOSS):
        y = 105 + 15 * n
        profit_and_loss += [(50, y, label), (390, y, value), (480, y, value)]
    return write_pdf(path, balance_sheet, profit_and_loss)


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def query(root: Path, sql: str) -> list[Any]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


@pytest.fixture
def scanned(samples: Path, tmp_path: Path) -> Path:
    """A PDF with no text layer (E104 without a reading)."""
    return Path(
        shutil.copy(samples / "broken" / "Tarang_FY2024-25_SCANNED.pdf", tmp_path / "scan.pdf")
    )


def test_reading_loads_a_scanned_pdf_when_the_gates_pass(
    root: Path, tmp_path: Path, scanned: Path, audit_query: Any
) -> None:
    reading = write_reading(tmp_path / "scan.reading.csv", BALANCE_SHEET)

    result = runner.invoke(app, ["ingest", "acme", str(scanned), "--reading", str(reading)])

    assert result.exit_code == 0, result.stderr
    assert "4 facts, 1 unmapped labels, 4 read by Claude (unverified)" in result.stdout
    assert query(
        root,
        "SELECT f.account, f.period, f.value, f.read_by, f.verified, r.page, r.raw_value_text "
        "FROM facts f JOIN raw_values r USING (raw_id) ORDER BY f.fact_id",
    ) == [
        ("cash", "FY25", Decimal("2850000.00"), "claude", False, 1, "28.50"),
        (None, "FY25", Decimal("7150000.00"), "claude", False, 1, "71.50"),
        ("total_assets", "FY25", Decimal("10000000.00"), "claude", False, 1, "100.00"),
        ("total_equity_and_liabilities", "FY25", Decimal("10000000.00"), "claude", False, 1)
        + ("100.00",),
    ]
    [(file_name, reading_path)] = query(root, "SELECT file_name, reading_path FROM documents")
    assert file_name == "scan.pdf"
    assert (root / reading_path).read_text(encoding="utf-8") == reading.read_text(encoding="utf-8")
    assert audit_query(
        "SELECT error_code FROM events WHERE stage = 'extract' AND level = 'warning'"
    ) == [("E104",)]
    assert audit_query("SELECT check_name, passed FROM validation_results ORDER BY check_name") == [
        ("balance_sheet", True)
    ]
    with connect(analytics_db_path(root)) as con:
        assert con.execute("SELECT unverified_claude_facts FROM companies").fetchall() == [(4,)]


def test_reading_that_fails_a_gate_is_rolled_back(
    root: Path, tmp_path: Path, scanned: Path
) -> None:
    unbalanced = [*BALANCE_SHEET[:3], (*BALANCE_SHEET[3][:5], "90.00", 1)]
    reading = write_reading(tmp_path / "scan.reading.csv", unbalanced)

    result = runner.invoke(app, ["ingest", "acme", str(scanned), "--reading", str(reading)])

    assert result.exit_code == 1
    assert "E401" in result.stdout
    assert query(root, "SELECT count(*) FROM facts") == [(0,)]
    assert query(root, "SELECT count(*) FROM documents") == [(0,)]
    error = json.loads((root / "review" / "acme" / "scan.pdf.error.json").read_text("utf-8"))
    assert (error["code"], error["reading"]) == ("E401", str(reading))
    assert error["to_fix"][-1].endswith(f'--reading "{reading}"')


def test_reading_fills_only_the_statement_fin_skipped(root: Path, tmp_path: Path) -> None:
    pdf = mixed_report(tmp_path / "ar25.pdf")
    reading = write_reading(tmp_path / "r1.csv", BALANCE_SHEET + PROFIT_AND_LOSS)

    result = runner.invoke(app, ["ingest", "acme", str(pdf), "--reading", str(reading)])

    assert result.exit_code == 0, result.stderr
    assert query(
        root,
        "SELECT r.statement, f.read_by, count(*) FROM facts f JOIN raw_values r USING (raw_id) "
        "GROUP BY ALL ORDER BY ALL",
    ) == [("balance_sheet", "fin", 8), ("profit_and_loss", "claude", 3)]


def test_reading_with_nothing_fin_did_not_read_is_e205(root: Path, tmp_path: Path) -> None:
    pdf = mixed_report(tmp_path / "ar25.pdf")
    reading = write_reading(tmp_path / "r1.csv", BALANCE_SHEET)

    result = runner.invoke(app, ["ingest", "acme", str(pdf), "--reading", str(reading)])

    assert result.exit_code == 1
    assert "E205" in result.stdout
    assert query(root, "SELECT count(*) FROM facts") == [(0,)]


def verify(*args: str) -> Any:
    return runner.invoke(app, ["verify", "acme", *args])


def test_verify_marks_agreeing_facts_and_reports_every_outcome(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    pdf = mixed_report(tmp_path / "ar25.pdf")
    first = write_reading(tmp_path / "r1.csv", BALANCE_SHEET + PROFIT_AND_LOSS)
    assert runner.invoke(app, ["ingest", "acme", str(pdf), "--reading", str(first)]).exit_code == 0

    # The reading the document was loaded with agrees, but verifies only fin's own facts.
    same = verify(str(pdf), "--reading", str(first))
    assert same.exit_code == 0, same.stderr
    assert "agree 7, differ 0, missing in fin 0, missing in reading 0" in same.stdout
    assert "marked verified: 4 facts (0 read by Claude)" in same.stdout
    assert "a second, independent reading is needed" in same.stdout

    # A second, independent reading (its own file) verifies the Claude-read facts too.
    second = write_reading(tmp_path / "r2.csv", PROFIT_AND_LOSS + BALANCE_SHEET)
    agreed = verify(str(pdf), "--reading", str(second), "--json")
    assert agreed.exit_code == 0, agreed.stderr
    payload = json.loads(agreed.stdout)
    assert (payload["counts"], payload["verified_claude_facts"]) == (
        {"agree": 7, "differ": 0, "missing_in_fin": 0, "missing_in_reading": 0},
        3,
    )
    assert query(
        root, "SELECT read_by, verified, count(*) FROM facts GROUP BY ALL ORDER BY ALL"
    ) == [("claude", True, 3), ("fin", False, 4), ("fin", True, 4)]  # FY24: not in the reading

    # A third reading: cash differs, security deposits left out, a line fin does not have.
    third = write_reading(
        tmp_path / "r3.csv",
        [
            (*BALANCE_SHEET[0][:5], "28.60", 1),
            *BALANCE_SHEET[2:],
            ("balance_sheet", "2025-03-31", "", "Equity", "Other equity", "60.00", 1),
        ],
    )
    differs = verify(str(pdf), "--reading", str(third))
    assert differs.exit_code == 0, differs.stderr  # findings, not errors
    assert "agree 2, differ 1, missing in fin 1, missing in reading 1" in differs.stdout
    assert (
        "differ             balance_sheet standalone FY25 'Cash and cash equivalents': "
        "fin 28.50 (page 1); reading 28.60 (page 1, reading line 2)"
    ) in differs.stdout
    assert "missing_in_fin     balance_sheet standalone FY25 'Other equity'" in differs.stdout
    assert "missing_in_reading balance_sheet standalone FY25 'Security deposits'" in differs.stdout
    cash = "SELECT verified FROM facts WHERE account = 'cash' AND period = 'FY25'"
    assert query(root, cash) == [(False,)]
    assert audit_query(
        "SELECT check_name, finding, label FROM findings WHERE other_file = 'r3.csv' ORDER BY ALL"
    ) == [
        ("verify_reading", "differ", "Cash and cash equivalents"),
        ("verify_reading", "missing_in_fin", "Other equity"),
        ("verify_reading", "missing_in_reading", "Security deposits"),
    ]
    assert audit_query(
        "SELECT fin_value, other_value, difference, fin_source, other_source FROM findings "
        "WHERE finding = 'differ'"
    ) == [
        (
            Decimal("2850000.00"),
            Decimal("2860000.00"),
            Decimal("10000.00"),
            "page 1",
            "page 1, reading line 2",
        )
    ]
    # Earlier verifies agreed throughout, so they recorded no findings.
    assert audit_query("SELECT count(*) FROM findings WHERE other_file <> 'r3.csv'") == [(0,)]


def test_verify_of_a_document_not_loaded_lists_every_line_missing(
    root: Path, tmp_path: Path, scanned: Path
) -> None:
    reading = write_reading(tmp_path / "r.csv", BALANCE_SHEET)

    result = verify(str(scanned), "--reading", str(reading), "--json")

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert (payload["doc_id"], payload["counts"]["missing_in_fin"]) == (None, 4)


def test_verify_rejects_an_invalid_reading_with_e205(
    root: Path, tmp_path: Path, scanned: Path
) -> None:
    reading = tmp_path / "bad.csv"
    reading.write_text("statement,label\nbalance_sheet,Cash\n", encoding="utf-8")

    result = verify(str(scanned), "--reading", str(reading))

    assert result.exit_code == 1
    assert "E205: bad.csv: missing columns scope, period_end" in result.stderr
