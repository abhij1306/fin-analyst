from pathlib import Path

import pytest

from fin.audit.errors import FileEncrypted
from fin.extract.text import write_notes_markdown

SAMPLES = Path(__file__).parent.parent / "fixtures" / "sample-financials"
FY24_PDF = SAMPLES / "clean/tarang/Tarang_Industrial_Services_Annual_Report_FY2023-24.pdf"
FY25_PDF = SAMPLES / "clean/tarang/Tarang_Industrial_Services_Annual_Report_FY2024-25.pdf"


def test_writes_auditor_report_and_notes_markdown(tmp_path: Path) -> None:
    written = write_notes_markdown(FY25_PDF, tmp_path / "notes" / "tarang")

    stem = FY25_PDF.stem
    assert [p.name for p in written] == [f"{stem}.auditor_report.md", f"{stem}.notes.md"]
    report = written[0].read_text(encoding="utf-8")
    assert report.startswith(
        f"Source: {FY25_PDF.name}, page(s) 2\n\n# INDEPENDENT AUDITOR'S REPORT\n"
    )
    assert (
        "## Emphasis of Matter\n\nWe draw attention to Note 10 to the financial statements, "
        in report
    )
    assert "receivables of ₹ 186.75 lakhs outstanding for more than 180 days" in report
    assert "## Basis for Opinion" in report

    notes = written[1].read_text(encoding="utf-8")
    assert notes.startswith(f"Source: {FY25_PDF.name}, page(s) 6\n\n# Notes to the financial")
    assert (
        "## 19. Related party transactions\n\n"
        "| Related party Relationship Nature | FY25 | FY24 |\n"
        "|---|---|---|\n"
        "| Tarang Logistics LLP Entity controlled by promoter Freight services purchased"
        " | 412.50 | 365.20 |\n"
    ) in notes
    assert (
        "## 20. Contingent liabilities not provided for\n\n"
        "| Particulars | FY25 | FY24 |\n"
        "|---|---|---|\n"
        "| Bank guarantees issued on behalf of the Company | 245.00 | 210.00 |\n"
        "| Disputed GST demand under appeal | 132.40 | 0.00 |\n"
    ) in notes


def test_fy24_auditor_report_has_no_emphasis_of_matter(tmp_path: Path) -> None:
    report, _ = write_notes_markdown(FY24_PDF, tmp_path)

    text = report.read_text(encoding="utf-8")
    assert "## Opinion" in text
    assert "Emphasis of Matter" not in text


def test_encrypted_pdf_raises_e102_and_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(FileEncrypted):
        write_notes_markdown(SAMPLES / "broken/Tarang_FY2024-25_ENCRYPTED.pdf", tmp_path / "out")
    assert not (tmp_path / "out").exists()
