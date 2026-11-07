"""Running Docling (fin.extract.docling_run) without Docling: its output is stood in for at the one
place Docling is called (`_convert`), so what fin does with it is tested fast."""

from pathlib import Path

import pytest

from fin.audit.errors import DoclingFailed, PeriodsNotDetected
from fin.extract import docling_run
from fin.extract.docling_pages import Cell, Table
from fin.extract.pdf import SkippedPage
from tests.synthetic_pdf import write_pdf

TABLE = Table(
    [
        [Cell("Particulars", True), Cell("31 March 2025", True), Cell("31 March 2024", True)],
        [Cell("Total assets"), Cell("100.00"), Cell("90.00")],
    ]
)


def test_a_scanned_page_is_read_when_its_title_follows_the_company_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(tmp_path / "scan.pdf", [], [])
    pages = {
        1: (
            [TABLE],
            [
                "Acme Industries Limited Consolidated Balance Sheet as at 31 March 2025",
                "( in Lakhs)",
            ],
        ),
        # A notes page names the balance sheet too, but is not one.
        2: ([TABLE], ["Notes forming part of the Balance Sheet", "( in Lakhs)"]),
    }
    monkeypatch.setattr(docling_run, "_convert", lambda path, numbers, result: pages)

    found = docling_run.read_scanned(path, 4)

    assert found is not None
    assert [(v.page, v.statement, v.scope, v.label, v.period, v.unit) for v in found.values] == [
        (1, "balance_sheet", "consolidated", "Total assets", "FY25", "lakh"),
        (1, "balance_sheet", "consolidated", "Total assets", "FY24", "lakh"),
    ]
    assert found.read_pages == [1]


def test_a_long_scanned_pdf_is_not_read(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", *([[]] * (docling_run.SCANNED_PAGE_LIMIT + 1)))

    assert docling_run.read_scanned(path, 4) is None


def test_docling_failing_leaves_the_pages_unread_with_e206(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_pdf(
        tmp_path / "report.pdf", [(50, 50, "Balance Sheet"), (50, 65, "(Rs. in Lakhs)")]
    )
    skipped = [SkippedPage(1, "balance_sheet", PeriodsNotDetected("page 1: no"))]

    def crash(path: Path, numbers: list[int], result: object) -> None:
        raise DoclingFailed("report.pdf: Docling failed on pages [1]: out of memory")

    monkeypatch.setattr(docling_run, "_convert", crash)

    found = docling_run.read_skipped(path, skipped, 4)

    assert found.values == []
    assert found.warnings == [("E206", "report.pdf: Docling failed on pages [1]: out of memory")]
    assert [u.as_dict() for u in found.unread] == [
        {
            "page": 1,
            "statement": "balance_sheet",
            "code": "E206",
            "reason": "page 1: no; Docling: report.pdf: Docling failed on pages [1]: out of memory",
        }
    ]
