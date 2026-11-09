from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from fin.audit.errors import FinError
from fin.extract.excel import extract_workbook, parse_period
from fin.extract.units import to_rupees, unit_in


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("31.03.2025", date(2025, 3, 31)),
        ("31/03/2024", date(2024, 3, 31)),
        ("As at 31-Mar-2025", date(2025, 3, 31)),
        ("Year ended 31-Mar-2024", date(2024, 3, 31)),
        ("For the year ended 31st March, 2023", date(2023, 3, 31)),
        ("March 31, 2025", date(2025, 3, 31)),
        (datetime(2025, 3, 31, 0, 0), date(2025, 3, 31)),
        ("Consolidated Balance Sheet as at 31-Mar-2025", None),
        ("Particulars", None),
        ("31.02.2025", None),
        (2025, None),
    ],
)
def test_parse_period(cell: object, expected: date | None) -> None:
    assert parse_period(cell) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("(₹ in Lakhs)", "lakh"),
        ("(Rs. in Crores)", "crore"),
        ("All amounts in INR thousands", "thousand"),
        ("(Amount in rupees)", "rupees"),
        ("₹ lacs", "lac"),
        ("Balance Sheet as at", None),
    ],
)
def test_unit_in(text: str, expected: str | None) -> None:
    assert unit_in(text) == expected


def test_to_rupees_is_exact() -> None:
    assert to_rupees(Decimal("13409.29"), "lakh") == Decimal("1340929000.00")
    assert to_rupees(Decimal("-0.07"), "crore") == Decimal("-700000.00")


def write_sheet(path: Path, rows: list[list[object]], title: str = "Balance Sheet") -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def error_code(path: Path) -> str:
    with pytest.raises(FinError) as caught:
        extract_workbook(path)
    return caught.value.code


def test_values_get_sheet_cell_period_section_and_rupees(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx",
        [
            ["ACME LIMITED"],
            ["Consolidated Balance Sheet"],
            ["(₹ in Lakhs)"],
            ["Particulars", "Note", "31.03.2025", "31.03.2024"],
            ["Current liabilities"],
            ["Borrowings", "5", "1,200.50", "(3.25)"],
            ["Trade payables", None, 10, "-"],
        ],
    )
    extraction = extract_workbook(path)
    assert extraction.skipped_sheets == []
    assert [
        (v.cell, v.statement, v.scope, v.section, v.label, v.raw, v.period_end, v.rupees)
        for v in extraction.values
    ] == [
        ("C6", "balance_sheet", "consolidated", "Current liabilities", "Borrowings", "1,200.50",
         date(2025, 3, 31), Decimal("120050000.00")),
        ("D6", "balance_sheet", "consolidated", "Current liabilities", "Borrowings", "(3.25)",
         date(2024, 3, 31), Decimal("-325000.00")),
        ("C7", "balance_sheet", "consolidated", "Current liabilities", "Trade payables", "10",
         date(2025, 3, 31), Decimal("1000000.00")),
        ("D7", "balance_sheet", "consolidated", "Current liabilities", "Trade payables", "-",
         date(2024, 3, 31), Decimal("0.00")),
    ]  # fmt: skip


def write_merged(
    path: Path, rows: list[list[object]], merges: list[str], title: str = "Results"
) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(row)
    for span in merges:
        sheet.merge_cells(span)
    book.save(path)
    return path


def test_merged_length_headings_give_each_column_its_length(tmp_path: Path) -> None:
    path = write_merged(
        tmp_path / "t.xlsx",
        [
            ["Statement of Profit and Loss"],
            ["(₹ in Lakhs)"],
            ["Particulars", "Quarter ended", None, "Six months ended", "Year ended"],
            ["", "31.03.2025", "31.03.2024", "30.09.2025", "31.03.2025"],
            ["Revenue", 1, 2, 3, 4],
        ],
        ["B3:C3"],
    )
    values = extract_workbook(path).values
    assert [(v.cell, v.period_end, v.months) for v in values] == [
        ("B5", date(2025, 3, 31), 3),
        ("C5", date(2024, 3, 31), 3),
        ("D5", date(2025, 9, 30), 6),
        ("E5", date(2025, 3, 31), 12),
    ]


def test_column_naming_no_length_on_an_interim_sheet_is_e202(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx",
        [
            ["Statement of Profit and Loss"],
            ["(₹ in Lakhs)"],
            ["Particulars", "Quarter ended 31.03.2025", "31.03.2024"],
            ["Revenue", 1, 2],
        ],
        title="Results",
    )
    assert error_code(path) == "E202"


def test_scope_headings_give_each_column_its_scope(tmp_path: Path) -> None:
    path = write_merged(
        tmp_path / "t.xlsx",
        [
            ["Statement of Profit and Loss"],
            ["(₹ in Lakhs)"],
            ["Particulars", "Standalone", None, "Consolidated", None],
            ["", "31.03.2025", "31.03.2024", "31.03.2025", "31.03.2024"],
            ["Revenue", 1, 2, 3, 4],
        ],
        ["B3:C3", "D3:E3"],
    )
    values = extract_workbook(path).values
    assert [(v.cell, v.scope) for v in values] == [
        ("B5", "standalone"),
        ("C5", "standalone"),
        ("D5", "consolidated"),
        ("E5", "consolidated"),
    ]


def test_column_without_scope_on_a_sheet_naming_both_is_e202(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx",
        [
            ["Statement of Profit and Loss"],
            ["(₹ in Lakhs)"],
            ["Particulars", "Standalone", None, "Consolidated"],
            ["", "31.03.2025", "31.03.2024", "31.03.2025"],
            ["Revenue", 1, 2, 3],
        ],
        title="Results",
    )
    assert error_code(path) == "E202"


def test_kestrel_formula_cells_use_saved_values(samples: Path) -> None:
    extraction = extract_workbook(
        samples / "clean" / "kestrel" / "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
    )
    total_income = [v for v in extraction.values if (v.sheet, v.cell) == ("Profit and Loss", "C9")]
    assert [(v.label, v.raw, v.rupees) for v in total_income] == [
        ("III. Total income (I + II)", "13462.93", Decimal("1346293000.00"))
    ]


def test_formula_without_saved_value_is_e204(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx",
        [["(₹ in Lakhs)"], ["Particulars", "31.03.2025"], ["Total assets", "=1+1"]],
    )
    assert error_code(path) == "E204"


def test_missing_unit_is_e203(samples: Path) -> None:
    assert error_code(samples / "broken" / "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx") == "E203"


def test_missing_period_header_is_e202(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx", [["(₹ in Lakhs)"], ["Particulars", "Current", "Previous"]]
    )
    assert error_code(path) == "E202"


def test_text_where_a_number_belongs_is_e204(tmp_path: Path) -> None:
    path = write_sheet(
        tmp_path / "t.xlsx",
        [["(₹ in Lakhs)"], ["Particulars", "31.03.2025"], ["Total assets", "see note"]],
    )
    assert error_code(path) == "E204"


def test_workbook_without_statements_is_e201(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "t.xlsx", [["Cover page"]], title="Cover")
    assert error_code(path) == "E201"


def test_not_a_workbook_is_e101(tmp_path: Path) -> None:
    path = tmp_path / "t.xlsx"
    path.write_text("not a zip", encoding="utf-8")
    assert error_code(path) == "E101"


def test_encrypted_workbook_is_e102(tmp_path: Path) -> None:
    path = tmp_path / "t.xlsx"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 504)
    assert error_code(path) == "E102"
