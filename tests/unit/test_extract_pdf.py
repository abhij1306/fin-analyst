import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest
from PIL import Image

from fin.audit.errors import (
    FileEncrypted,
    FileUnreadable,
    NoTablesFound,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)
from fin.extract.pdf import extract_pdf, render_crop
from tests.synthetic_pdf import write_pdf

SAMPLES = Path(__file__).parent.parent / "fixtures" / "sample-financials"
TARANG = json.loads((SAMPLES / "answer_key.json").read_text(encoding="utf-8"))["companies"][
    "tarang"
]
FY24_PDF = SAMPLES / "clean/tarang/Tarang_Industrial_Services_Annual_Report_FY2023-24.pdf"
FY25_PDF = SAMPLES / "clean/tarang/Tarang_Industrial_Services_Annual_Report_FY2024-25.pdf"
STATEMENT_PAGE_KEY = {
    "balance_sheet": "bs",
    "profit_and_loss": "pl",
    "cash_flow": "cf",
    "notes": "notes",
}


def answer_key_values(document: dict) -> Counter[tuple[str, str, Decimal]]:
    """(label, fiscal year, rupees) for every statement line and note row the key expects."""
    labels = {account: label for label, account in TARANG["labels"].items()}
    # The key's labels map is keyed by label, so it holds the current-liabilities "Borrowings" only.
    labels["lt_borrowings"] = "(a) Financial liabilities - Borrowings"
    expected: Counter[tuple[str, str, Decimal]] = Counter()
    for fy in document["fiscal_years"]:
        for account, rupees in TARANG["facts"]["standalone"][fy].items():
            expected[(labels[account], fy, Decimal(rupees))] += 1
        for row in TARANG["notes"]["related_party_rupees"]:
            label = f"{row['party']} {row['relationship']} {row['nature']}"
            expected[(label, fy, Decimal(row["amounts"][fy]))] += 1
        for row in TARANG["notes"]["contingent_liabilities_rupees"]:
            expected[(row["item"], fy, Decimal(row["amounts"][fy]))] += 1
    return expected


@pytest.mark.parametrize("document", TARANG["documents"], ids=["FY24 no lines", "FY25 rules"])
def test_every_value_matches_the_answer_key(document: dict) -> None:
    result = extract_pdf(SAMPLES / document["file"])

    found = Counter((v.label, v.fiscal_year, v.rupees) for v in result.values)
    assert found == answer_key_values(document)
    # The titles do not say standalone or consolidated; ingest applies defaults.scope.
    assert {v.scope for v in result.values} == {None}
    assert result.skipped == []
    assert [*dict.fromkeys(v.period for v in result.values)] == document["fiscal_years"]
    assert {v.unit for v in result.values} == {"lakh"}
    for value in result.values:
        assert value.page == document["pages"][STATEMENT_PAGE_KEY[value.statement]]


def test_each_value_keeps_its_raw_text_section_and_note() -> None:
    values = extract_pdf(FY24_PDF).values

    interest = [v for v in values if v.label == "Adjustments for: Interest income"]
    assert [(v.fiscal_year, v.raw, v.value) for v in interest] == [
        ("FY24", "(56.67)", Decimal("-56.67")),
        ("FY23", "(47.22)", Decimal("-47.22")),
    ]
    borrowings = [
        (v.section, v.note, v.raw)
        for v in values
        if v.label == "(a) Financial liabilities - Borrowings" and v.fiscal_year == "FY24"
    ]
    assert borrowings == [
        ("(2) Non-current liabilities", "4", "300.00"),
        ("(3) Current liabilities", "5", "400.00"),
    ]
    gst = next(v for v in values if v.label == "Disputed GST demand under appeal")
    assert (gst.statement, gst.section) == ("notes", "20. Contingent liabilities not provided for")


@pytest.mark.parametrize("path", [FY24_PDF, FY25_PDF], ids=["FY24", "FY25"])
def test_bbox_of_every_value_frames_its_raw_text(path: Path) -> None:
    values = extract_pdf(path).values

    with pdfplumber.open(path) as pdf:
        for v in values:
            assert pdf.pages[v.page - 1].crop(v.bbox).extract_text() == v.raw


def test_render_crop_writes_a_png_of_the_row_band(tmp_path: Path) -> None:
    value = next(v for v in extract_pdf(FY25_PDF).values if v.label == "TOTAL ASSETS")

    out = render_crop(FY25_PDF, value.page, value.bbox, tmp_path / "crop.png")

    with Image.open(out) as image:
        assert image.format == "PNG"
        assert image.width == 1241  # the full A4 page width, 595.28 pt, at 150 dpi
        assert 100 < image.height < 130  # the value's line plus 24 pt above and below


def test_encrypted_pdf_raises_e102() -> None:
    with pytest.raises(FileEncrypted) as caught:
        extract_pdf(SAMPLES / "broken/Tarang_FY2024-25_ENCRYPTED.pdf")
    assert caught.value.code == "E102"


def test_scanned_pdf_raises_e104() -> None:
    with pytest.raises(ScannedPdf) as caught:
        extract_pdf(SAMPLES / "broken/Tarang_FY2024-25_SCANNED.pdf")
    assert caught.value.code == "E104"


def test_file_that_is_not_a_pdf_raises_e101(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    path.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(FileUnreadable) as caught:
        extract_pdf(path)
    assert caught.value.code == "E101"


def test_crore_unit_financial_year_ranges_and_nil_dashes(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "pl.pdf",
        [
            (50, 50, "Statement of Profit and Loss for the year ended 31 March 2025"),
            (50, 70, "(Rs. in Crores)"),
            (50, 90, "Particulars"),
            (400, 90, "2024-25"),
            (480, 90, "2023-24"),
            (50, 110, "Revenue from operations"),
            (400, 110, "1,234.50"),
            (480, 110, "987.25"),
            (50, 130, "Exceptional items"),
            (433, 130, "-"),
            (500, 130, "(1.05)"),
        ],
    )

    result = extract_pdf(path)

    assert {v.unit for v in result.values} == {"crore"}
    assert [*dict.fromkeys(v.period for v in result.values)] == ["FY25", "FY24"]
    assert [(v.label, v.fiscal_year, v.rupees) for v in result.values] == [
        ("Revenue from operations", "FY25", Decimal("12345000000.00")),
        ("Revenue from operations", "FY24", Decimal("9872500000.00")),
        ("Exceptional items", "FY25", Decimal(0)),
        ("Exceptional items", "FY24", Decimal("-10500000.00")),
    ]


def test_pdf_without_statements_raises_e201(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "letter.pdf", [(50, 50, "Dear shareholders, a fine year.")])
    with pytest.raises(NoTablesFound) as caught:
        extract_pdf(path)
    assert caught.value.code == "E201"


def test_statement_without_period_headers_raises_e202(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "bs.pdf",
        [
            (50, 50, "Balance Sheet"),
            (50, 70, "(Rs. in Lakhs)"),
            (50, 110, "Inventories"),
            (400, 110, "178.54"),
            (480, 110, "150.94"),
        ],
    )
    with pytest.raises(PeriodsNotDetected) as caught:
        extract_pdf(path)
    assert caught.value.code == "E202"


def test_statement_without_unit_raises_e203(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "bs.pdf",
        [
            (50, 50, "Balance Sheet as at 31 March 2024"),
            (390, 90, "31 March 2024"),
            (470, 90, "31 March 2023"),
            (50, 110, "Inventories"),
            (420, 110, "178.54"),
            (500, 110, "150.94"),
        ],
    )
    with pytest.raises(UnitNotDetected) as caught:
        extract_pdf(path)
    assert caught.value.code == "E203"
