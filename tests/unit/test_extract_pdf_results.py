"""PDF extraction of quarterly and half-year results (SEBI Reg 33 statements), rebuilt as tiny
synthetic PDFs with the layouts seen in real filings."""

from datetime import date
from pathlib import Path

import pytest

from fin.audit.errors import PeriodsNotDetected
from fin.extract.pdf import extract_pdf
from fin.extract.periods import period_of
from tests.synthetic_pdf import Page, write_pdf


def rows(path: Path) -> list[tuple[int, str, str | None, str, int, str, str]]:
    """(page, statement, scope, period, months, label, raw) for every statement value."""
    return [
        (v.page, v.statement, v.scope, v.period, v.months, v.label, v.raw)
        for v in extract_pdf(path).values
        if v.statement != "notes"
    ]


@pytest.mark.parametrize(
    ("end", "months", "expected"),
    [
        (date(2025, 3, 31), 12, "FY25"),
        (date(2025, 6, 30), 3, "FY26-Q1"),
        (date(2025, 3, 31), 3, "FY25-Q4"),
        (date(2025, 9, 30), 6, "FY26-H1"),
        (date(2026, 3, 31), 6, "FY26-H2"),
        (date(2025, 12, 31), 9, "FY26-9M"),
    ],
)
def test_period_names(end: date, months: int, expected: str) -> None:
    assert period_of(end, months, where="test") == expected


def test_a_period_that_does_not_fit_the_fiscal_year_is_e202() -> None:
    with pytest.raises(PeriodsNotDetected) as caught:
        period_of(date(2025, 5, 31), 3, where="page 2")
    assert caught.value.message == (
        "page 2: a 3-month period ending 2025-05-31 does not line up with a fiscal year starting "
        "in month 4"
    )


def test_q1_results_keep_the_march_quarter_apart_from_the_march_year(tmp_path: Path) -> None:
    # "Quarter ended" heads three columns, "Year ended" one; two columns end on 31.03.2025.
    path = write_pdf(
        tmp_path / "q1.pdf",
        [
            (50, 50, "Statement of Unaudited Standalone Financial Results for the quarter ended"),
            (50, 65, "(Rs. in Lakhs)"),
            (274, 80, "Quarter ended"),
            (400, 80, "Year ended"),
            (50, 92, "Particulars"),
            (220, 92, "30.06.2025"),
            (280, 92, "31.03.2025"),
            (340, 92, "30.06.2024"),
            (400, 92, "31.03.2025"),
            (220, 104, "Unaudited"),
            (280, 104, "Audited"),
            (340, 104, "Unaudited"),
            (400, 104, "Audited"),
            (50, 120, "Revenue from operations"),
            (225, 120, "1,100.00"),
            (285, 120, "1,050.00"),
            (345, 120, "1,000.00"),
            (405, 120, "4,100.00"),
            (50, 135, "Profit after tax"),
            (225, 135, "800.00"),
            (285, 135, "750.00"),
            (345, 135, "700.00"),
            (405, 135, "2,900.00"),
        ],
    )

    result = extract_pdf(path)

    assert result.fiscal_years == ["FY26-Q1", "FY25-Q4", "FY25-Q1", "FY25"]
    assert rows(path) == [
        (1, "profit_and_loss", "standalone", "FY26-Q1", 3, "Revenue from operations", "1,100.00"),
        (1, "profit_and_loss", "standalone", "FY25-Q4", 3, "Revenue from operations", "1,050.00"),
        (1, "profit_and_loss", "standalone", "FY25-Q1", 3, "Revenue from operations", "1,000.00"),
        (1, "profit_and_loss", "standalone", "FY25", 12, "Revenue from operations", "4,100.00"),
        (1, "profit_and_loss", "standalone", "FY26-Q1", 3, "Profit after tax", "800.00"),
        (1, "profit_and_loss", "standalone", "FY25-Q4", 3, "Profit after tax", "750.00"),
        (1, "profit_and_loss", "standalone", "FY25-Q1", 3, "Profit after tax", "700.00"),
        (1, "profit_and_loss", "standalone", "FY25", 12, "Profit after tax", "2,900.00"),
    ]
    assert {v.fiscal_year for v in result.values if v.period == "FY26-Q1"} == {"FY26"}


def test_both_scopes_side_by_side_half_year_balance_sheet_and_split_figures(
    tmp_path: Path,
) -> None:
    results: Page = [
        (50, 50, "Statement of Unaudited Financial Results for the quarter and half year ended"),
        (50, 65, "(Rs. in Lakhs)"),
        (263, 78, "Standalone"),
        (437, 78, "Consolidated"),
        (226, 90, "Quarter ended"),
        (312, 90, "Half year ended"),
        (406, 90, "Quarter ended"),
        (492, 90, "Half year ended"),
        # d-Mon-yy dates; columns 1-2 and 4-5 are quarters, 3 and 6 half years.
        (205, 102, "30-Sep-25"),
        (265, 102, "30-Sep-24"),
        (325, 102, "30-Sep-25"),
        (385, 102, "30-Sep-25"),
        (445, 102, "30-Sep-24"),
        (505, 102, "30-Sep-25"),
        (50, 120, "Revenue from operations"),
        # "1,210." and "00" a point apart are one figure; so are "(" and "5.00)".
        (207, 120, "1,210."),
        (236, 120, "00"),
        (267, 120, "1,020.00"),
        (327, 120, "2,310.00"),
        (387, 120, "1,300.00"),
        (447, 120, "1,100.00"),
        (507, 120, "2,500.00"),
        (50, 135, "Exceptional items"),
        (207, 135, "("),
        (212, 135, "5.00)"),
        (267, 135, "-"),
        (327, 135, "(5.00)"),
        (387, 135, "(5.00)"),
        (447, 135, "-"),
        (507, 135, "(5.00)"),
    ]
    balance_sheet: Page = [
        (50, 50, "Notes:"),
        (50, 62, "1. Statement of Assets and Liabilities as at 30.09.2025"),
        (50, 77, "(Rs. in Lakhs)"),
        (248, 90, "Standalone"),
        (432, 90, "Consolidated"),
        (190, 102, "As at 30.09.2025"),
        (280, 102, "As at 31.03.2025"),
        (380, 102, "As at 30.09.2025"),
        (470, 102, "As at 31.03.2025"),
        (50, 120, "TOTAL ASSETS"),
        (210, 120, "500.00"),
        (300, 120, "450.00"),
        (400, 120, "600.00"),
        (490, 120, "550.00"),
    ]
    path = write_pdf(tmp_path / "q2.pdf", results, balance_sheet)

    assert rows(path) == [
        (1, "profit_and_loss", "standalone", "FY26-Q2", 3, "Revenue from operations", "1,210.00"),
        (1, "profit_and_loss", "standalone", "FY25-Q2", 3, "Revenue from operations", "1,020.00"),
        (1, "profit_and_loss", "standalone", "FY26-H1", 6, "Revenue from operations", "2,310.00"),
        (1, "profit_and_loss", "consolidated", "FY26-Q2", 3, "Revenue from operations", "1,300.00"),
        (1, "profit_and_loss", "consolidated", "FY25-Q2", 3, "Revenue from operations", "1,100.00"),
        (1, "profit_and_loss", "consolidated", "FY26-H1", 6, "Revenue from operations", "2,500.00"),
        (1, "profit_and_loss", "standalone", "FY26-Q2", 3, "Exceptional items", "(5.00)"),
        (1, "profit_and_loss", "standalone", "FY25-Q2", 3, "Exceptional items", "-"),
        (1, "profit_and_loss", "standalone", "FY26-H1", 6, "Exceptional items", "(5.00)"),
        (1, "profit_and_loss", "consolidated", "FY26-Q2", 3, "Exceptional items", "(5.00)"),
        (1, "profit_and_loss", "consolidated", "FY25-Q2", 3, "Exceptional items", "-"),
        (1, "profit_and_loss", "consolidated", "FY26-H1", 6, "Exceptional items", "(5.00)"),
        # A balance sheet is a point in time: 30 Sep closes the first half, 31 March the year.
        (2, "balance_sheet", "standalone", "FY26-H1", 6, "TOTAL ASSETS", "500.00"),
        (2, "balance_sheet", "standalone", "FY25", 12, "TOTAL ASSETS", "450.00"),
        (2, "balance_sheet", "consolidated", "FY26-H1", 6, "TOTAL ASSETS", "600.00"),
        (2, "balance_sheet", "consolidated", "FY25", 12, "TOTAL ASSETS", "550.00"),
    ]


def test_results_columns_that_do_not_name_their_length_are_e202(tmp_path: Path) -> None:
    # Without "Quarter ended" / "Year ended", a 31 March column could be the quarter or the year:
    # it is never taken for a year.
    path = write_pdf(
        tmp_path / "q4.pdf",
        [
            (50, 50, "Statement of Audited Standalone Financial Results for the quarter and year"),
            (50, 65, "(Rs. in Lakhs)"),
            (280, 92, "31.03.2025"),
            (400, 92, "31.03.2024"),
            (50, 120, "Revenue from operations"),
            (285, 120, "1,050.00"),
            (405, 120, "4,100.00"),
        ],
    )

    with pytest.raises(PeriodsNotDetected) as caught:
        extract_pdf(path)

    assert caught.value.code == "E202"
    assert caught.value.message == (
        "q4.pdf: no statement page could be read: page 1: results statement column ending "
        "2025-03-31 does not say whether it is a quarter, half year or year"
    )
