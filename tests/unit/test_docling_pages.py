"""Docling's table grids read with fin's rules (fin.extract.docling_pages). The grids are built by
hand the way Docling gives them (a spanning cell repeats in each column): no Docling needed."""

from decimal import Decimal

import pytest

from fin.audit.errors import NoTablesFound, ParseError, PeriodsNotDetected, UnitNotDetected
from fin.extract.docling_pages import Cell, PageContext, Table, read_tables, top_left


def grid(*rows: list[str], header_rows: int = 0) -> list[list[Cell]]:
    """Cells of row i, column j sit at x = 10j, top = 20i (8 x 9 points)."""
    return [
        [
            Cell(
                text, header=i < header_rows, bbox=(10.0 * j, 20.0 * i, 10.0 * j + 8, 20.0 * i + 9)
            )
            for j, text in enumerate(row)
        ]
        for i, row in enumerate(rows)
    ]


def page(statement: str = "balance_sheet", **changes: object) -> PageContext:
    fields: dict[str, object] = {
        "page": 7,
        "statement": statement,
        "scope": None,
        "unit": "lakh",
        "results": False,
    }
    fields.update(changes)
    return PageContext(**fields)  # type: ignore[arg-type]


def rows(values: list) -> list[tuple]:
    return [(v.label, v.section, v.period, v.scope, v.raw, v.rupees) for v in values]


SIDE_BY_SIDE = grid(
    ["", "Standalone", "Standalone", "Consolidated", "Consolidated"],
    ["Particulars", "As at 30.09.2025", "As at 31.03.2025", "As at 30.09.2025", "As at 31.03.2025"],
    ["ASSETS", "", "", "", ""],
    ["Non-current assets", "", "", "", ""],
    ["Property, plant and equipment", "60.00", "50.00", "70.00", "55.00"],
    ["(a) Financial assets", "", "", "", ""],
    ["Loans", "5.00", "-", "(6.00)", "6.00"],
    header_rows=2,
)


def test_side_by_side_standalone_and_consolidated_columns_get_their_scope_and_period() -> None:
    values = read_tables(page(), [Table(SIDE_BY_SIDE)], start_month=4)

    assert rows(values) == [
        (
            "Property, plant and equipment",
            "Non-current assets",
            "FY26-H1",
            "standalone",
            "60.00",
            Decimal("6000000.00"),
        ),
        (
            "Property, plant and equipment",
            "Non-current assets",
            "FY25",
            "standalone",
            "50.00",
            Decimal("5000000.00"),
        ),
        (
            "Property, plant and equipment",
            "Non-current assets",
            "FY26-H1",
            "consolidated",
            "70.00",
            Decimal("7000000.00"),
        ),
        (
            "Property, plant and equipment",
            "Non-current assets",
            "FY25",
            "consolidated",
            "55.00",
            Decimal("5500000.00"),
        ),
        # "(a) Financial assets" is a sub-heading: the section stays the part heading.
        ("Loans", "Non-current assets", "FY26-H1", "standalone", "5.00", Decimal("500000.00")),
        ("Loans", "Non-current assets", "FY25", "standalone", "-", Decimal("0.00")),
        ("Loans", "Non-current assets", "FY26-H1", "consolidated", "(6.00)", Decimal("-600000.00")),
        ("Loans", "Non-current assets", "FY25", "consolidated", "6.00", Decimal("600000.00")),
    ]
    assert {(v.statement, v.page, v.unit, v.months) for v in values} == {
        ("balance_sheet", 7, "lakh", 6),
        ("balance_sheet", 7, "lakh", 12),
    }


def test_the_page_title_gives_the_scope_when_no_column_names_one() -> None:
    table = grid(
        ["Particulars", "Note", "31 March 2025", "31 March 2024"],
        ["Revenue from operations", "21", "1,234.50", "1,100.00"],
        ["Total income", "", "1,300.00", "1,150.00"],
        header_rows=1,
    )

    values = read_tables(page("profit_and_loss", scope="consolidated"), [Table(table)], 4)

    assert [(v.label, v.note, v.period, v.scope, v.raw) for v in values] == [
        ("Revenue from operations", "21", "FY25", "consolidated", "1,234.50"),
        ("Revenue from operations", "21", "FY24", "consolidated", "1,100.00"),
        ("Total income", None, "FY25", "consolidated", "1,300.00"),
        ("Total income", None, "FY24", "consolidated", "1,150.00"),
    ]


def test_results_columns_take_their_length_from_the_header() -> None:
    table = grid(
        ["", "Quarter ended", "Quarter ended", "Year ended"],
        ["Particulars", "30.09.2025", "30.06.2025", "31.03.2025"],
        ["Revenue from operations", "500.00", "450.00", "1,800.00"],
    )

    values = read_tables(page("profit_and_loss", results=True), [Table(table)], 4)

    assert [(v.period, v.months, v.raw) for v in values] == [
        ("FY26-Q2", 3, "500.00"),
        ("FY26-Q1", 3, "450.00"),
        ("FY25", 12, "1,800.00"),
    ]


def test_a_results_column_that_names_no_length_leaves_the_page_unread() -> None:
    table = grid(["Particulars", "30.09.2025", "30.06.2025"], ["Revenue", "500.00", "450.00"])

    context, tables = page("profit_and_loss", results=True), [Table(table)]

    with pytest.raises(PeriodsNotDetected) as caught:
        read_tables(context, tables, 4)

    assert caught.value.code == "E202"
    assert "does not say whether it is a quarter" in caught.value.message


def test_two_columns_with_the_same_period_and_scope_leave_the_page_unread() -> None:
    table = grid(
        ["Particulars", "As at 31.03.2025", "As at 31.03.2025"],
        ["Loans", "5.00", "6.00"],
        header_rows=1,
    )

    context, tables = page(), [Table(table)]

    with pytest.raises(PeriodsNotDetected) as caught:
        read_tables(context, tables, 4)

    assert caught.value.code == "E202"
    assert caught.value.message == (
        "page 7 (Docling): two columns hold the same period (FY25 unscoped, FY25 unscoped)"
    )


def test_no_unit_on_the_page_or_in_the_table_leaves_the_page_unread() -> None:
    context, tables = page(unit=None), [Table(SIDE_BY_SIDE)]

    with pytest.raises(UnitNotDetected) as caught:
        read_tables(context, tables, 4)

    assert caught.value.code == "E203"


def test_a_unit_printed_in_the_table_is_used() -> None:
    table = grid(
        ["(₹ in crores)", "31 March 2025", "31 March 2024"],
        ["Loans", "1.50", "1.00"],
        header_rows=1,
    )

    values = read_tables(page(unit=None), [Table(table)], 4)

    assert [(v.unit, v.rupees) for v in values] == [
        ("crore", Decimal("15000000.00")),
        ("crore", Decimal("10000000.00")),
    ]


def test_a_cell_that_is_not_one_figure_leaves_the_page_unread() -> None:
    table = grid(["Particulars", "31 March 2025", "31 March 2024"], ["Loans", "12.3 4", "1.00"])

    context, tables = page(), [Table(table)]

    with pytest.raises(ParseError) as caught:
        read_tables(context, tables, 4)

    assert caught.value.code == "E204"


def test_a_figure_with_an_unpaired_bracket_leaves_the_page_unread() -> None:
    table = grid(["Particulars", "31 March 2025", "31 March 2024"], ["Loans", "(4.00", "1.00"])

    context, tables = page(), [Table(table)]

    with pytest.raises(ParseError, match="unpaired bracket"):
        read_tables(context, tables, 4)


def test_a_page_whose_tables_have_no_period_columns_is_not_read() -> None:
    table = grid(["Name", "Designation"], ["A Person", "Director"])

    context, tables = page(), [Table(table)]

    with pytest.raises(NoTablesFound) as caught:
        read_tables(context, tables, 4)

    assert caught.value.code == "E201"


def test_an_unlabelled_row_takes_its_heading_as_label() -> None:
    table = grid(
        ["Particulars", "31 March 2025", "31 March 2024"],
        ["Total equity", "", ""],
        ["", "90.00", "80.00"],
    )

    values = read_tables(page(), [Table(table)], 4)

    assert [(v.label, v.section, v.raw) for v in values] == [
        ("Total equity", "Total equity", "90.00"),
        ("Total equity", "Total equity", "80.00"),
    ]


def test_each_value_keeps_its_cell_bbox() -> None:
    values = read_tables(page(), [Table(SIDE_BY_SIDE)], 4)

    assert values[0].bbox == (10.0, 80.0, 18.0, 89.0)  # row 4, column 1 of the grid


def test_docling_bottom_left_boxes_become_top_left() -> None:
    assert top_left(50.0, 800.0, 120.0, 790.0, bottom_left=True, height=842.0) == (
        50.0,
        42.0,
        120.0,
        52.0,
    )
    assert top_left(50.0, 42.0, 120.0, 52.0, bottom_left=False, height=842.0) == (
        50.0,
        42.0,
        120.0,
        52.0,
    )
