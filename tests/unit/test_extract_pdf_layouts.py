"""PDF extraction on layouts seen in real Indian annual reports, rebuilt as tiny synthetic PDFs."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fin.audit.errors import PeriodsNotDetected
from fin.extract.pdf import extract_pdf, period_end_in
from tests.synthetic_pdf import write_pdf


def rows(path: Path) -> list[tuple[str, str, str | None, str, str]]:
    """(label, section, note, fiscal year, raw) for every value."""
    return [(v.label, v.section, v.note, v.fiscal_year, v.raw) for v in extract_pdf(path).values]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("31 March 2024", date(2024, 3, 31)),
        ("As at March 31, 2025", date(2025, 3, 31)),
        ("March 31,2024", date(2024, 3, 31)),
        ("31st March, 2025", date(2025, 3, 31)),
        ("31.03.2025", date(2025, 3, 31)),
        ("Year Ended 31-03- 2025", date(2025, 3, 31)),
        ("31-Mar-2025", date(2025, 3, 31)),
        ("Mar 31, 2025", date(2025, 3, 31)),
        ("2023-24", date(2024, 3, 31)),
        ("Quarter ended 30.06.2025", date(2025, 6, 30)),
        ("April 1, 2023", None),  # an opening balance date, not a period end
        ("2023-25", None),
        ("Particulars", None),
    ],
)
def test_period_end_in_reads_indian_date_styles(text: str, expected: date | None) -> None:
    assert period_end_in(text) == expected


def test_month_first_header_over_three_lines_notes_wrapped_labels_and_subtotals(
    tmp_path: Path,
) -> None:
    path = write_pdf(
        tmp_path / "bs.pdf",
        [
            (50, 50, "Rane Test Limited"),
            (50, 70, "STANDALONE BALANCE SHEET"),
            (50, 85, "AS AT MARCH 31, 2025"),
            (50, 100, "(All amounts are in INR Crores unless otherwise stated)"),
            (400, 120, "As at"),
            (480, 120, "As at"),
            (50, 125, "Particulars"),
            (330, 125, "Note"),
            (380, 130, "March 31, 2025"),
            (460, 130, "March 31, 2024"),
            (50, 145, "Non-current assets"),
            (60, 160, "(a) Property, plant and equipment"),
            (335, 160, "2"),
            (400, 160, "635.09"),
            (480, 160, "632.87"),
            (60, 175, "(b) Financial assets"),
            (70, 190, "(i) Investments"),
            (335, 190, "6"),
            (400, 190, "45.93"),
            (480, 190, "33.93"),
            (400, 205, "980.21"),
            (480, 205, "946.18"),
            (50, 220, "Non-current liabilities"),
            (70, 235, "(i) Borrowings"),
            (335, 235, "17A"),
            (400, 235, "217.30"),
            (480, 235, "304.98"),
            (60, 250, "(a) Total outstanding dues of micro enterprises and small"),
            (400, 254, "16.63"),
            (480, 254, "28.19"),
            (60, 258, "enterprises"),
            (50, 275, "TOTAL ASSETS"),
            (400, 275, "2,264.17"),
            (480, 275, "2,104.60"),
        ],
    )

    result = extract_pdf(path)

    assert result.fiscal_years == ["FY25", "FY24"]
    assert {(v.scope, v.unit) for v in result.values} == {("standalone", "crore")}
    assert rows(path) == [
        ("(a) Property, plant and equipment", "Non-current assets", "2", "FY25", "635.09"),
        ("(a) Property, plant and equipment", "Non-current assets", "2", "FY24", "632.87"),
        ("(i) Investments", "Non-current assets", "6", "FY25", "45.93"),
        ("(i) Investments", "Non-current assets", "6", "FY24", "33.93"),
        # An unlabelled subtotal takes the heading it closes.
        ("Non-current assets", "Non-current assets", None, "FY25", "980.21"),
        ("Non-current assets", "Non-current assets", None, "FY24", "946.18"),
        ("(i) Borrowings", "Non-current liabilities", "17A", "FY25", "217.30"),
        ("(i) Borrowings", "Non-current liabilities", "17A", "FY24", "304.98"),
        (
            "(a) Total outstanding dues of micro enterprises and small enterprises",
            "Non-current liabilities",
            None,
            "FY25",
            "16.63",
        ),
        (
            "(a) Total outstanding dues of micro enterprises and small enterprises",
            "Non-current liabilities",
            None,
            "FY24",
            "28.19",
        ),
        ("TOTAL ASSETS", "Non-current liabilities", None, "FY25", "2,264.17"),
        ("TOTAL ASSETS", "Non-current liabilities", None, "FY24", "2,104.60"),
    ]
    assert result.values[-2].rupees == Decimal("22641700000.00")


def test_header_dates_split_over_lines_and_headings_printed_with_dashes(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "bs.pdf",
        [
            (50, 50, "Balance Sheet as at March 31, 2024"),
            (50, 65, "(Rs. in Lakhs)"),
            (50, 80, "Particulars"),
            (300, 80, "Note"),
            (380, 80, "As at March 31,"),
            (470, 80, "As at March 31,"),
            (300, 90, "No."),
            (395, 90, "2024"),
            (485, 90, "2023"),
            (50, 100, "Shareholders Funds"),
            (60, 110, "(a) Share Capital"),
            (305, 110, "2"),
            (395, 110, "871.61"),
            (485, 110, "1.00"),
            (60, 125, "Current assets"),
            (430, 125, "-"),
            (520, 125, "-"),
            (80, 140, "(b) Trade Receivables"),
            (305, 140, "14"),
            (395, 140, "1,181.73"),
            (485, 140, "487.87"),
            (395, 155, "3,196.49"),
            (485, 155, "896.69"),
            (80, 170, "Gratuity Paid"),
            (430, 170, "-"),
            (520, 170, "-"),
            (80, 185, "Interest Paid"),
            (395, 185, "(98.85)"),
            (485, 185, "(56.13)"),
        ],
    )

    result = extract_pdf(path)

    assert result.fiscal_years == ["FY24", "FY23"]
    assert {v.scope for v in result.values} == {None}  # the title does not say
    assert rows(path) == [
        ("(a) Share Capital", "Shareholders Funds", "2", "FY24", "871.61"),
        ("(a) Share Capital", "Shareholders Funds", "2", "FY23", "1.00"),
        # "Current assets - -" heads the indented lines below it, so it is a heading, not a row.
        ("(b) Trade Receivables", "Current assets", "14", "FY24", "1,181.73"),
        ("(b) Trade Receivables", "Current assets", "14", "FY23", "487.87"),
        ("Current assets", "Current assets", None, "FY24", "3,196.49"),
        ("Current assets", "Current assets", None, "FY23", "896.69"),
        # Nothing indented below "Gratuity Paid - -": a line item that is nil.
        ("Gratuity Paid", "Current assets", None, "FY24", "-"),
        ("Gratuity Paid", "Current assets", None, "FY23", "-"),
        ("Interest Paid", "Current assets", None, "FY24", "(98.85)"),
        ("Interest Paid", "Current assets", None, "FY23", "(56.13)"),
    ]


def test_each_page_keeps_its_scope_continued_pages_and_skipped_pages(tmp_path: Path) -> None:
    unit = (50, 65, "(Rs. in Lakhs)")
    path = write_pdf(
        tmp_path / "report.pdf",
        [
            (50, 50, "STANDALONE STATEMENT OF PROFIT AND LOSS"),
            unit,
            (380, 85, "31.03.2025"),
            (470, 85, "31.03.2024"),
            (50, 105, "Revenue from operations"),
            (390, 105, "1,000.00"),
            (480, 105, "900.00"),
        ],
        # Same statement, no unit and no header: carries on from the page before.
        [
            (50, 50, "STANDALONE STATEMENT OF PROFIT AND LOSS"),
            (50, 80, "Profit for the year"),
            (390, 80, "100.00"),
            (480, 80, "90.00"),
        ],
        [
            (50, 50, "CONSOLIDATED STATEMENT OF PROFIT AND LOSS"),
            unit,
            (370, 85, "31st March, 2025"),
            (470, 85, "31st March, 2024"),
            (50, 105, "Revenue from operations"),
            (390, 105, "1,500.00"),
            (480, 105, "1,400.00"),
        ],
        # A statement page without period columns is skipped, not fatal.
        [
            (50, 50, "Consolidated Balance Sheet as at 31 March 2025"),
            unit,
            (50, 90, "Inventories"),
            (390, 90, "10.00"),
            (480, 90, "9.00"),
        ],
        # Text that only mentions a statement is not a statement page.
        [
            (50, 50, "MANAGEMENT DISCUSSION AND ANALYSIS"),
            (50, 65, "Balance Sheet :"),
            unit,
            (380, 85, "31.03.2025"),
            (470, 85, "31.03.2024"),
            (50, 105, "Net debt"),
            (390, 105, "5.00"),
            (480, 105, "6.00"),
        ],
    )

    result = extract_pdf(path)

    assert [(v.scope, v.page, v.label, v.fiscal_year, v.raw) for v in result.values] == [
        ("standalone", 1, "Revenue from operations", "FY25", "1,000.00"),
        ("standalone", 1, "Revenue from operations", "FY24", "900.00"),
        ("standalone", 2, "Profit for the year", "FY25", "100.00"),
        ("standalone", 2, "Profit for the year", "FY24", "90.00"),
        ("consolidated", 3, "Revenue from operations", "FY25", "1,500.00"),
        ("consolidated", 3, "Revenue from operations", "FY24", "1,400.00"),
    ]
    assert [(s.page, s.statement, s.error.code) for s in result.skipped] == [
        (4, "balance_sheet", "E202")
    ]


def test_quarterly_results_fail_with_e202_and_say_why(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "q2.pdf",
        [(50, 50, "Statement of Unaudited Standalone Financial Results for the quarter ended")],
        [
            (50, 50, "Standalone Statement of Cash Flows for the half year ended September 30"),
            (50, 65, "(Rs. in Lakhs)"),
            (380, 85, "30.09.2025"),
            (470, 85, "30.09.2024"),
            (50, 105, "Net cash from operating activities"),
            (390, 105, "10.00"),
            (480, 105, "9.00"),
        ],
    )

    with pytest.raises(PeriodsNotDetected) as caught:
        extract_pdf(path)

    assert caught.value.code == "E202"
    assert "is a quarterly or half-year results statement" in caught.value.message
    assert "period columns end 2025-09-30, 2024-09-30" in caught.value.message


def test_landscape_spread_is_read_as_two_half_pages(tmp_path: Path) -> None:
    header = [(640, 75, "31st March 2025"), (730, 75, "31st March 2024")]
    unit = (440, 55, "(All amounts in lakhs unless stated otherwise)")
    path = write_pdf(
        tmp_path / "spread.pdf",
        [
            (40, 40, "INDEPENDENT AUDITOR'S REPORT"),
            (40, 60, "We have audited the financial statements."),
            (440, 40, "Standalone Balance Sheet as at 31st March 2025"),
            unit,
            *header,
            (440, 95, "Total Assets"),
            (650, 95, "66,181.56"),
            (740, 95, "52,372.00"),
        ],
        [
            # Left half: the balance sheet carries on, untitled.
            (40, 40, "Particulars"),
            (240, 40, "31st March 2025"),
            (330, 40, "31st March 2024"),
            (40, 60, "Total equity and liabilities"),
            (250, 60, "66,181.56"),
            (340, 60, "52,372.00"),
            (440, 40, "Standalone Statement of Profit and Loss for the year ended"),
            unit,
            *header,
            (440, 95, "Revenue from operations"),
            (650, 95, "36,676.99"),
            (740, 95, "35,246.15"),
        ],
        width=842,
        height=595,
    )

    values = extract_pdf(path).values

    assert [(v.statement, v.scope, v.page, v.label, v.raw) for v in values] == [
        ("balance_sheet", "standalone", 1, "Total Assets", "66,181.56"),
        ("balance_sheet", "standalone", 1, "Total Assets", "52,372.00"),
        ("balance_sheet", "standalone", 2, "Total equity and liabilities", "66,181.56"),
        ("balance_sheet", "standalone", 2, "Total equity and liabilities", "52,372.00"),
        ("profit_and_loss", "standalone", 2, "Revenue from operations", "36,676.99"),
        ("profit_and_loss", "standalone", 2, "Revenue from operations", "35,246.15"),
    ]


def test_far_right_nil_dash_and_a_figure_printed_in_two_pieces(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "pl.pdf",
        [
            (50, 50, "Statement of Profit and Loss for the year ended 31 March 2025"),
            (50, 65, "(Rs. in Lakhs)"),
            (380, 85, "31 March 2025"),
            (470, 85, "31 March 2024"),
            (50, 105, "Purchase of traded goods"),
            (400, 105, "456.21"),
            (560, 105, "-"),  # right-aligned well past the end of its header
            (50, 125, "Total income"),
            (390, 125, "76,189.2"),
            (428.92, 125, "1", "F2"),  # touches the piece before it, in another font
            (480, 125, "64,794.94"),
        ],
    )

    assert [(label, fy, raw) for label, _, _, fy, raw in rows(path)] == [
        ("Purchase of traded goods", "FY25", "456.21"),
        ("Purchase of traded goods", "FY24", "-"),
        ("Total income", "FY25", "76,189.21"),
        ("Total income", "FY24", "64,794.94"),
    ]
