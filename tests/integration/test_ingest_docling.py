"""The Docling add-on at ingest: the statement pages pdfplumber skips go to Docling when it is on;
otherwise they stay unread with a notice naming the two ways to read them.

The test marked `docling` runs the real Docling (CI job `docling`, `uv sync --extra docling`); the
others need no Docling."""

import csv
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import fin.extract.docling_run
from fin.audit.errors import DoclingFailed
from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import TEMPLATES_DIR
from fin.paths import company_db_path
from tests.synthetic_pdf import PROFIT_AND_LOSS, Page, write_pdf

runner = CliRunner()
X = (290, 380, 490, 580)  # standalone H1, FY25, consolidated H1, FY25
# label, standalone 30.09.2025, standalone 31.03.2025, consolidated 30.09.2025, 31.03.2025
ROWS = (
    ("Property, plant and equipment", "60.00", "50.00", "70.00", "55.00"),
    ("Loans", "5.00", "5.00", "6.00", "6.00"),
    ("Total non-current assets", "65.00", "55.00", "76.00", "61.00"),
    "Current assets",
    ("Cash and cash equivalents", "35.00", "45.00", "(4.00)", "-"),
    ("Total current assets", "35.00", "45.00", "(4.00)", "-"),
    ("Total assets", "100.00", "100.00", "72.00", "61.00"),
)


def side_by_side() -> Page:
    """A half-year statement of assets and liabilities with standalone and consolidated columns side
    by side: two columns hold each period, so pdfplumber skips it (E202)."""
    page: Page = [
        (50, 40, "Statement of Assets and Liabilities"),
        (50, 55, "(Rs. in Lakhs)"),
        (330, 75, "Standalone"),
        (530, 75, "Consolidated"),
        *[(x, 90, "As at") for x in X],
        *[(x, 102, date) for x, date in zip(X, ["30.09.2025", "31.03.2025"] * 2, strict=True)],
        (50, 125, "ASSETS"),
        (50, 140, "Non-current assets"),
    ]
    for n, row in enumerate(ROWS):
        y = 155 + 15 * n
        if isinstance(row, str):
            page.append((50, y, row))
            continue
        page.append((50, y, row[0]))
        page += [(x, y, figure) for x, figure in zip(X, row[1:], strict=True)]
    return page


def report(tmp_path: Path) -> Path:
    """Page 1: a profit and loss pdfplumber reads. Page 2: the side-by-side balance sheet."""
    return write_pdf(tmp_path / "acme_h1.pdf", PROFIT_AND_LOSS, side_by_side(), width=760)


def init(*flags: str) -> None:
    result = runner.invoke(app, ["init", *flags])
    assert result.exit_code == 0, result.stdout


def facts(root: Path, sql: str) -> list[Any]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def extract_events(audit_query: Callable[..., list[Any]]) -> list[str]:
    return [m for (m,) in audit_query("SELECT message FROM events WHERE stage = 'extract'")]


def test_with_docling_off_the_skipped_page_stays_unread_with_a_notice(
    data_root: Path, tmp_path: Path, audit_query: Callable[..., list[Any]]
) -> None:
    init()

    result = runner.invoke(app, ["ingest", "acme", str(report(tmp_path))])

    assert result.exit_code == 0, result.stdout
    assert "not read: balance_sheet page 2: E202" in result.stdout
    assert facts(data_root, "SELECT DISTINCT read_by FROM facts") == [("fin",)]
    assert (
        "pages 2 not read, and the Docling add-on is off ([pdf] docling = false in config.toml): "
        "install it (`./install.sh --docling`) or give a Claude reading (`fin ingest --reading`)"
    ) in extract_events(audit_query)


def test_with_docling_on_but_not_installed_the_page_stays_unread_with_a_notice(
    data_root: Path,
    tmp_path: Path,
    audit_query: Callable[..., list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init("--docling")
    monkeypatch.setattr(fin.extract.docling_run, "installed", lambda: False)

    result = runner.invoke(app, ["ingest", "acme", str(report(tmp_path))])

    assert result.exit_code == 0, result.stdout
    assert "not read: balance_sheet page 2: E202" in result.stdout
    assert (
        "pages 2 not read, and the Docling add-on is not installed: install it "
        "(`./install.sh --docling`) or give a Claude reading (`fin ingest --reading`)"
    ) in extract_events(audit_query)


def test_a_file_with_no_readable_page_fails_as_before_when_docling_is_not_installed(
    data_root: Path,
    tmp_path: Path,
    audit_query: Callable[..., list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init("--docling")
    monkeypatch.setattr(fin.extract.docling_run, "installed", lambda: False)
    path = write_pdf(tmp_path / "acme_bs.pdf", side_by_side(), width=760)

    result = runner.invoke(app, ["ingest", "acme", str(path)])

    assert result.exit_code == 1
    error = json.loads((data_root / "review/acme/acme_bs.pdf.error.json").read_text("utf-8"))
    assert (error["code"], [p["page"] for p in error["unread_pages"]]) == ("E202", [1])
    assert "pages 1 not read, and the Docling add-on is not installed" in (
        " ".join(extract_events(audit_query))
    )


def test_docling_failing_is_a_warning_and_the_document_still_loads(
    data_root: Path,
    tmp_path: Path,
    audit_query: Callable[..., list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init("--docling")
    monkeypatch.setattr(fin.extract.docling_run, "installed", lambda: True)

    def crash(path: Path, numbers: list[int], result: object) -> None:
        raise DoclingFailed("acme_h1.pdf: Docling failed on pages [2]: out of memory")

    monkeypatch.setattr(fin.extract.docling_run, "_convert", crash)

    result = runner.invoke(app, ["ingest", "acme", str(report(tmp_path))])

    assert result.exit_code == 0, result.stdout
    assert "not read: balance_sheet page 2: E206 " in result.stdout
    assert facts(data_root, "SELECT DISTINCT read_by FROM facts") == [("fin",)]
    assert audit_query(
        "SELECT level, error_code, message FROM events WHERE error_code = 'E206'"
    ) == [("warning", "E206", "acme_h1.pdf: Docling failed on pages [2]: out of memory")]


@pytest.mark.docling
@pytest.mark.timeout(900)
def test_docling_reads_the_page_pdfplumber_skipped(
    data_root: Path, tmp_path: Path, audit_query: Callable[..., list[Any]]
) -> None:
    init("--docling")

    result = runner.invoke(app, ["ingest", "acme", str(report(tmp_path))])

    assert result.exit_code == 0, result.stdout
    assert "not read:" not in result.stdout
    assert "24 read by Docling" in result.stdout
    assert facts(
        data_root, "SELECT read_by, count(*) FROM facts GROUP BY read_by ORDER BY read_by"
    ) == [("docling", 24), ("fin", 40)]
    loaded = facts(
        data_root,
        "SELECT r.raw_label, f.scope, f.period, f.value, r.raw_value_text "
        "FROM facts f JOIN raw_values r USING (raw_id) WHERE f.read_by = 'docling' "
        "AND r.raw_label IN ('Loans', 'Cash and cash equivalents') ORDER BY ALL",
    )
    assert loaded == [
        ("Cash and cash equivalents", "consolidated", "FY25", Decimal("0.00"), "-"),
        ("Cash and cash equivalents", "consolidated", "FY26-H1", Decimal("-400000.00"), "(4.00)"),
        ("Cash and cash equivalents", "standalone", "FY25", Decimal("4500000.00"), "45.00"),
        ("Cash and cash equivalents", "standalone", "FY26-H1", Decimal("3500000.00"), "35.00"),
        ("Loans", "consolidated", "FY25", Decimal("600000.00"), "6.00"),
        ("Loans", "consolidated", "FY26-H1", Decimal("600000.00"), "6.00"),
        ("Loans", "standalone", "FY25", Decimal("500000.00"), "5.00"),
        ("Loans", "standalone", "FY26-H1", Decimal("500000.00"), "5.00"),
    ]
    pages = facts(
        data_root,
        "SELECT DISTINCT r.page, r.section FROM facts f JOIN raw_values r USING (raw_id) "
        "WHERE f.read_by = 'docling' AND r.raw_label = 'Loans'",
    )
    assert pages == [(2, "Non-current assets")]
    # The figure's box in fin's convention (points from the top-left), as pdfplumber gives it: the
    # standalone half-year "5.00" is printed at x 290 on the line at y 170.
    (bbox,) = facts(
        data_root,
        "SELECT r.bbox FROM facts f JOIN raw_values r USING (raw_id) WHERE f.read_by = 'docling' "
        "AND r.raw_label = 'Loans' AND f.scope = 'standalone' AND f.period = 'FY26-H1'",
    )[0]
    assert [round(x, 1) for x in json.loads(bbox)] == [290.0, 162.8, 309.5, 172.1]
    assert any(
        m.startswith("24 figures read by Docling from pages 2 (pdfplumber could not read them)")
        for m in extract_events(audit_query)
    )


SHORT_STATEMENT = {"balance_sheet": "bs", "profit_and_loss": "pl", "cash_flow": "cf"}


@pytest.mark.docling
@pytest.mark.timeout(1800)
def test_docling_reads_a_scanned_report_by_ocr(
    data_root: Path, samples: Path, answer_key: dict[str, Any]
) -> None:
    """The scanned copy of Tarang FY2024-25 (no text layer, E104 without the add-on): every figure
    of its three statements equals the answer key, read by Docling's OCR, on the right page and
    period. OCR can spell a label differently on another platform (it then waits for review,
    unmapped), so each mapped figure must be the key's account and every figure the key's value."""
    init("--docling")
    scanned = samples / "broken" / "Tarang_FY2024-25_SCANNED.pdf"

    result = runner.invoke(app, ["ingest", "tarang", str(scanned)])

    assert result.exit_code == 0, result.stdout
    key = answer_key["companies"]["tarang"]
    document = next(d for d in key["documents"] if d["file"].endswith("FY2024-25.pdf"))
    with (TEMPLATES_DIR / "schedule_iii.csv").open(encoding="utf-8") as handle:
        statements = {row["account"]: row["statement"] for row in csv.DictReader(handle)}
    with connect(company_db_path(data_root, "tarang")) as con:
        rows = con.execute(
            "SELECT r.page, f.account, f.fiscal_year, f.value, f.read_by "
            "FROM facts f JOIN raw_values r USING (raw_id)"
        ).fetchall()
    expected = {
        (document["pages"][SHORT_STATEMENT[statements[account]]], account, year, Decimal(value))
        for year in document["fiscal_years"]
        for account, value in key["facts"]["standalone"][year].items()
    }
    assert {read_by for *_, read_by in rows} == {"docling"}
    assert len(rows) == len(expected) == 108
    assert sorted((page, year, value) for page, _, year, value, _ in rows) == sorted(
        (page, year, value) for page, _, year, value in expected
    )
    mapped = {(page, account, year, value) for page, account, year, value, _ in rows if account}
    assert mapped <= expected
    assert len(mapped) >= 100
