"""The PDF add-on's reading flow (fin.extract.addon), with a stand-in engine: what fin does with an
engine's tables and text is the same for every engine, and is tested here without one."""

from pathlib import Path
from types import SimpleNamespace

from fin.audit.errors import ParseError, PdfAddonFailed, PeriodsNotDetected
from fin.extract import addon
from fin.extract.addon import Found, PageRead, Request
from fin.extract.pdf_model import SkippedPage
from fin.extract.pdf_tables import Cell, Table
from tests.synthetic_pdf import write_pdf

TABLE = Table(
    [
        [Cell("Particulars", True), Cell("31 March 2025", True), Cell("31 March 2024", True)],
        [Cell("Total assets"), Cell("100.00"), Cell("90.00")],
    ]
)


def engine(convert: object) -> SimpleNamespace:
    return SimpleNamespace(NAME="fake", LABEL="Fake", convert=convert)


def skipped_report(tmp_path: Path) -> tuple[Path, list[SkippedPage]]:
    path = write_pdf(
        tmp_path / "report.pdf", [(50, 50, "Balance Sheet"), (50, 65, "(Rs. in Lakhs)")]
    )
    return path, [SkippedPage(1, "balance_sheet", PeriodsNotDetected("page 1: no"))]


def test_the_skipped_pages_go_to_the_engine_once(tmp_path: Path) -> None:
    path, skipped = skipped_report(tmp_path)
    requests: list[Request] = []

    def convert(request: Request, notices: list[str]) -> Found:
        requests.append(request)
        return {1: PageRead([TABLE], ["Balance Sheet"])}

    found = addon.read_skipped(engine(convert), path, skipped, 4, tmp_path / "cache")

    assert requests == [Request(path, [1], tmp_path / "cache")]
    assert (found.reader, found.read_pages) == ("fake", [1])
    assert [(v.label, v.period, v.unit, v.raw) for v in found.values] == [
        ("Total assets", "FY25", "lakh", "100.00"),
        ("Total assets", "FY24", "lakh", "90.00"),
    ]
    assert found.notices == ["2 figures read by Fake from pages 1 (pdfplumber could not read them)"]


def test_a_scanned_page_is_read_when_its_title_follows_the_company_name(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", [], [])
    pages = {
        1: PageRead(
            [TABLE],
            [
                "Acme Industries Limited Consolidated Balance Sheet as at 31 March 2025",
                "( in Lakhs)",
            ],
        ),
        # A notes page names the balance sheet too, but is not one.
        2: PageRead([TABLE], ["Notes forming part of the Balance Sheet", "( in Lakhs)"]),
    }
    seen: list[Request] = []

    def convert(request: Request, notices: list[str]) -> Found:
        seen.append(request)
        return pages

    found = addon.read_scanned(engine(convert), path, 4, tmp_path)

    assert found is not None
    assert seen[0].pages == [1, 2]
    assert [(v.page, v.statement, v.scope, v.label, v.period, v.unit) for v in found.values] == [
        (1, "balance_sheet", "consolidated", "Total assets", "FY25", "lakh"),
        (1, "balance_sheet", "consolidated", "Total assets", "FY24", "lakh"),
    ]
    assert found.read_pages == [1]


def test_a_scanned_page_the_engine_could_not_read_is_listed_as_unread(tmp_path: Path) -> None:
    """One page read, one the engine failed on: the failed page is not dropped for lack of a
    title, so the report lists it."""
    path = write_pdf(tmp_path / "scan.pdf", [], [])
    pages = {
        1: PageRead([TABLE], ["Balance Sheet as at 31 March 2025", "( in Lakhs)"]),
        2: PageRead(error=PdfAddonFailed("page 2: Fake could not parse it: page timed out")),
    }

    found = addon.read_scanned(engine(lambda request, notices: pages), path, 4, tmp_path)

    assert found is not None
    assert found.read_pages == [1]
    assert [u.as_dict() for u in found.unread] == [
        {
            "page": 2,
            "statement": "unknown",
            "code": "E206",
            "reason": "page 2: Fake could not parse it: page timed out",
        }
    ]


def test_a_long_scanned_pdf_is_not_read(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", *([[]] * (addon.SCANNED_PAGE_LIMIT + 1)))

    assert addon.read_scanned(engine(None), path, 4, tmp_path) is None


def test_the_engine_failing_leaves_the_pages_unread_with_e206(tmp_path: Path) -> None:
    path, skipped = skipped_report(tmp_path)

    def crash(request: Request, notices: list[str]) -> Found:
        raise PdfAddonFailed("report.pdf: Fake failed on pages [1]: out of memory")

    found = addon.read_skipped(engine(crash), path, skipped, 4, tmp_path)

    assert found.values == []
    assert found.warnings == [("E206", "report.pdf: Fake failed on pages [1]: out of memory")]
    assert [u.as_dict() for u in found.unread] == [
        {
            "page": 1,
            "statement": "balance_sheet",
            "code": "E206",
            "reason": "page 1: no; Fake: report.pdf: Fake failed on pages [1]: out of memory",
        }
    ]


def test_a_page_the_engine_could_not_read_stays_unread_with_its_reason(tmp_path: Path) -> None:
    path, skipped = skipped_report(tmp_path)

    def convert(request: Request, notices: list[str]) -> Found:
        return {1: PageRead(error=ParseError("page 1: a cell is under no column (Fake)"))}

    found = addon.read_skipped(engine(convert), path, skipped, 4, tmp_path)

    assert found.values == []
    assert found.warnings == []
    assert [u.as_dict() for u in found.unread] == [
        {
            "page": 1,
            "statement": "balance_sheet",
            "code": "E204",
            "reason": "page 1: no; Fake: page 1: a cell is under no column (Fake)",
        }
    ]


def test_a_misread_table_names_the_engine(tmp_path: Path) -> None:
    path, skipped = skipped_report(tmp_path)
    bad = Table([[Cell("Particulars"), Cell("31 March 2025")], [Cell("Total 100.00"), Cell("1")]])

    found = addon.read_skipped(
        engine(lambda request, notices: {1: PageRead([bad])}), path, skipped, 4, tmp_path
    )

    assert "(Fake)" in found.unread[0].error.message


def test_subset_keeps_only_the_pages_asked_for(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "three.pdf", [(50, 50, "one")], [(50, 50, "two")], [])

    copy = tmp_path / "copy.pdf"
    copy.write_bytes(addon.subset(path, [2, 3]).getvalue())

    assert addon.page_count(copy) == 2
