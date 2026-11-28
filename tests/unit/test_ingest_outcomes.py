"""Ingest outcomes on real documents (SPEC-04), each rebuilt as a synthetic fixture: a unit
sentence with no unit word, a statement with no unit line (held with "which unit?", then loaded with
the user's answer), a scanned page and skipped pages with no PDF add-on (the add-ons offered), an
.xls workbook, and a row of values with no label (only its sheet held)."""

import json
import re
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

from fin.audit.logger import Run
from fin.cli import app
from fin.db.conn import connect
from fin.extract.excel import extract_workbook
from fin.extract.fallback import offered
from fin.paths import company_db_path
from fin.query.run import query
from tests.synthetic_pdf import BALANCE_SHEET, write_pdf

runner = CliRunner()
FIXTURES = Path(__file__).parent.parent / "fixtures"
SCANNED = FIXTURES / "sample-financials" / "broken" / "Tarang_FY2024-25_SCANNED.pdf"
HEADER = ["Particulars", "Note", "31.03.2025", "31.03.2024"]


@pytest.fixture
def root(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("CLAUDECODE", raising=False)  # the user runs the commands
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def workbook(path: Path, *sheets: tuple[str, list[list[object]]]) -> Path:
    book = Workbook()
    book.remove(book.worksheets[0])
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


def balance_sheet(unit_line: str | None) -> tuple[str, list[list[object]]]:
    rows: list[list[object]] = [["Balance Sheet"], *([[unit_line]] if unit_line else []), HEADER]
    return "Balance Sheet", [*rows, ["Borrowings", "5", 1200.5, 1000], ["Inventories", None, 10, 9]]


def rows(root: Path, sql: str) -> list[tuple[object, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def ingest(*args: str) -> str:
    result = runner.invoke(app, ["ingest", "acme", *args])
    return f"exit {result.exit_code}\n{result.output}"


def test_a_workbook_with_no_unit_line_is_held_with_the_question(root: Path, tmp_path: Path) -> None:
    path = workbook(tmp_path / "bs.xlsx", balance_sheet(None))

    out = ingest(str(path))

    assert out.startswith("exit 0\n"), out
    assert "held: bs.xlsx: which unit? no unit line" in out
    assert "on sheets 'Balance Sheet'" in out
    assert "Nothing loaded: fin does not guess a unit" in out
    assert "--unit lakhs|crores|millions|thousands|rupees" in out
    assert not company_db_path(root, "acme").exists()
    reason = json.loads((root / "review/acme/bs.xlsx.error.json").read_text("utf-8"))
    assert (reason["code"], reason["question"]) == ("E203", "which unit?")
    assert reason["answers"] == ["lakhs", "crores", "millions", "thousands", "rupees"]


def test_the_users_unit_is_stored_and_shown_where_the_figures_are_sourced(
    root: Path, tmp_path: Path
) -> None:
    path = workbook(tmp_path / "bs.xlsx", balance_sheet(None))

    out = ingest(str(path), "--unit", "lakhs")

    assert out.startswith("exit 0\n") and "loaded as doc_id 1" in out, out
    assert rows(root, "SELECT unit_given, unit_given_by FROM documents") == [("lakh", "user")]
    assert rows(root, "SELECT DISTINCT raw_unit, rupees_per_unit FROM raw_values") == [
        ("lakh (unit given by user)", 100000)
    ]
    [(fact_id, value)] = rows(
        root,
        "SELECT f.fact_id, f.value FROM facts f JOIN raw_values r USING (raw_id) "
        "WHERE r.raw_label = 'Borrowings' AND r.cell LIKE 'C%'",
    )
    assert value == Decimal("120050000.00")
    trace = runner.invoke(app, ["audit", "acme", "--fact", str(fact_id)])
    assert "1200.5 lakh (unit given by user)" in trace.output
    sql = f"SELECT fact_id, value FROM facts WHERE fact_id = {fact_id}"
    result = query(Run("sql", {}, root), "acme", None, sql, evidence=True)
    page = (root / result["evidence_path"]).read_text(encoding="utf-8")
    assert "lakh (unit given by user)" in page


def test_claude_entering_the_users_answer_is_recorded(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    path = workbook(tmp_path / "bs.xlsx", balance_sheet(None))

    ingest(str(path), "--unit", "crores")

    assert rows(root, "SELECT unit_given, unit_given_by FROM documents") == [("crore", "ai:claude")]
    assert rows(root, "SELECT DISTINCT raw_unit FROM raw_values") == [
        ("crore (unit given by user, entered by Claude)",)
    ]


def test_a_sheet_with_its_own_unit_line_keeps_it(root: Path, tmp_path: Path) -> None:
    profit = (
        "Profit and Loss",
        [["Statement of Profit and Loss"], HEADER, ["Revenue", None, 5, 4]],
    )
    path = workbook(tmp_path / "fs.xlsx", balance_sheet("(₹ in Crores)"), profit)

    ingest(str(path), "--unit", "lakhs")

    assert rows(root, "SELECT DISTINCT sheet, raw_unit FROM raw_values ORDER BY sheet") == [
        ("Balance Sheet", "(₹ in Crores)"),
        ("Profit and Loss", "lakh (unit given by user)"),
    ]


def test_a_unit_no_sheet_needed_is_refused_with_e207(root: Path, tmp_path: Path) -> None:
    path = workbook(tmp_path / "bs.xlsx", balance_sheet("(₹ in Lakhs)"))

    out = ingest(str(path), "--unit", "crores")

    assert out.startswith("exit 1\n"), out
    assert "E207: bs.xlsx: --unit crore was given, but every statement sheet or page names" in out
    assert not company_db_path(root, "acme").exists()


def plain(output: str) -> str:
    """A usage error's text without the colour codes, panel border and line wrapping Typer adds."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", output).replace("\N{BOX DRAWINGS LIGHT VERTICAL}", " ")
    return " ".join(text.split())


def test_unit_needs_one_file_and_a_known_answer(root: Path, tmp_path: Path) -> None:
    out = plain(ingest(str(tmp_path), "--unit", "lakhs"))
    assert out.startswith("exit 2") and "--unit needs PATH to be the one file" in out, out
    path = workbook(tmp_path / "bs.xlsx", balance_sheet(None))
    out = plain(ingest(str(path), "--unit", "lakh"))
    assert out.startswith("exit 2") and "use one of: lakhs, crores" in out, out


def test_the_unit_sentence_without_a_unit_word_loads_in_rupees(root: Path, tmp_path: Path) -> None:
    path = workbook(
        tmp_path / "bs.xlsx", balance_sheet("(All amounts in INR, unless otherwise stated)")
    )

    out = ingest(str(path))

    assert "loaded as doc_id 1" in out, out
    assert rows(root, "SELECT DISTINCT rupees_per_unit FROM raw_values") == [(1,)]


def test_values_with_no_label_hold_only_their_sheet_and_name_the_cells(
    root: Path, tmp_path: Path
) -> None:
    profit = (
        "Profit and Loss",
        [["Statement of Profit and Loss"], ["(₹ in Lakhs)"], HEADER, [None, None, 5, 4]],
    )
    path = workbook(tmp_path / "fs.xlsx", balance_sheet("(₹ in Lakhs)"), profit)

    out = ingest(str(path))

    assert "loaded as doc_id 1 version 1: 4 facts" in out, out
    reason = "fs.xlsx sheet 'Profit and Loss' row 4: values with no label in cells C4, D4"
    assert f"not read: profit_and_loss sheet 'Profit and Loss': E204 {reason}" in out
    assert "no PDF add-on" not in out  # no PDF page was skipped
    [(unread,)] = rows(root, "SELECT unread_pages FROM documents")
    assert json.loads(str(unread)) == [
        {
            "page": None,
            "sheet": "Profit and Loss",
            "statement": "profit_and_loss",
            "code": "E204",
            "reason": f"{reason}; the sheet was not read",
        }
    ]


def test_values_with_no_label_on_the_only_sheet_fail_naming_the_cells(
    root: Path, tmp_path: Path
) -> None:
    title, sheet = balance_sheet("(₹ in Lakhs)")
    path = workbook(tmp_path / "bs.xlsx", (title, [*sheet, [None, None, 7, 8]]))

    out = ingest(str(path))

    assert out.startswith("exit 1\n"), out
    assert "E204: bs.xlsx sheet 'Balance Sheet' row 6: values with no label in cells C6, D6" in out
    assert "not read: balance_sheet sheet 'Balance Sheet': E204" in out
    reason = json.loads((root / "review/acme/bs.xlsx.error.json").read_text("utf-8"))
    assert [u["sheet"] for u in reason["unread_pages"]] == ["Balance Sheet"]


def test_an_xls_workbook_is_read_like_an_xlsx(root: Path) -> None:
    path = FIXTURES / "xls" / "statements.xls"

    values = extract_workbook(path).values

    assert [(v.sheet, v.cell, v.label, v.raw, v.period_end, v.scope) for v in values[:4]] == [
        ("Balance Sheet", "C7", "Borrowings", "1200.5", date(2025, 3, 31), "standalone"),
        ("Balance Sheet", "D7", "Borrowings", "(3.25)", date(2024, 3, 31), "standalone"),
        ("Balance Sheet", "C8", "Trade payables", "10", date(2025, 3, 31), "standalone"),
        ("Balance Sheet", "D8", "Trade payables", "-", date(2024, 3, 31), "standalone"),
    ]
    assert values[0].rupees == Decimal("120050000.00")
    assert [(v.sheet, v.label, v.rupees) for v in values[4:]] == [
        ("Profit and Loss", "Revenue from operations", Decimal("50000000.00")),
        ("Profit and Loss", "Revenue from operations", Decimal("45075000.00")),
    ]
    assert "loaded as doc_id 1 version 1: 6 facts" in ingest(str(path))


def no_unit_page() -> list[tuple[float, float, str]]:
    return [
        (50, 50, "Balance Sheet as at 31 March 2024"),
        (390, 90, "31 March 2024"),
        (470, 90, "31 March 2023"),
        (50, 110, "Inventories"),
        (420, 110, "178.54"),
        (500, 110, "150.94"),
    ]


def test_a_pdf_page_with_no_unit_line_is_held_then_read_in_the_users_unit(
    root: Path, tmp_path: Path
) -> None:
    path = write_pdf(tmp_path / "bs.pdf", no_unit_page())

    held = ingest(str(path))
    loaded = ingest(str(path), "--unit", "crores")

    assert (
        "held: bs.pdf: which unit? no unit line such as '₹ in Lakhs' on statement pages 1" in held
    )
    assert "loaded as doc_id 1" in loaded, loaded
    assert rows(root, "SELECT raw_value_text, raw_unit, page FROM raw_values ORDER BY raw_id") == [
        ("178.54", "crore (unit given by user)", 1),
        ("150.94", "crore (unit given by user)", 1),
    ]


def test_a_scanned_pdf_with_no_add_on_offers_the_add_ons_that_ocr(
    root: Path, tmp_path: Path
) -> None:
    path = shutil.copy(SCANNED, tmp_path / "scan.pdf")

    out = ingest(str(path))

    assert "failed (E104" in out, out
    assert 'no PDF add-on is chosen ([pdf] parser = "none")' in out
    assert "Docling: local, nothing leaves this machine; about 1.4 GB" in out
    assert "LlamaParse: cloud, uploads the pages it reads to LlamaParse" in out
    assert "LiteParse" not in out  # fin does not use its OCR
    assert offered(scanned=True) == ["docling", "llamaparse"]


def test_skipped_pages_with_no_add_on_offer_every_add_on(root: Path, tmp_path: Path) -> None:
    no_periods = [(50, 50, "Statement of Profit and Loss"), (50, 70, "(Rs. in Lakhs)")]
    no_periods += [(50, 110, "Revenue from operations"), (400, 110, "178.54")]
    path = write_pdf(tmp_path / "fs.pdf", BALANCE_SHEET, no_periods, width=760)

    out = ingest(str(path))

    assert "loaded as doc_id 1" in out, out
    assert "not read: profit_and_loss page 2: E202" in out
    assert "One can read these pages: Docling: local, nothing leaves this machine" in out
    assert "LiteParse: local, nothing leaves this machine" in out
    assert "LlamaParse: cloud" in out
    assert "Install one with `fin parser use <name>`" in out
