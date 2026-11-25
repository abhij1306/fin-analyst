"""LiteParse (fin.extract.liteparse_run) without LiteParse: its layout blocks, recorded from real
LiteParse runs on synthetic pages (tests/fixtures/pdf_addon/liteparse_pages.json), are read into
fin's grids and then by fin's rules."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fin.audit.errors import ParseError, PeriodsNotDetected
from fin.extract import liteparse_run
from fin.extract.pdf_tables import Cell, PageContext, read_tables

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_addon" / "liteparse_pages.json"


def ns(value: Any) -> Any:
    """JSON as LiteParse's objects (attributes, not keys)."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [ns(v) for v in value]
    return value


def recorded(name: str) -> list[Any]:
    return ns(json.loads(FIXTURE.read_text(encoding="utf-8"))[name])


def context(page: int, statement: str = "balance_sheet", results: bool = False) -> PageContext:
    return PageContext(page, statement, None, "lakh", results, "LiteParse")


def test_a_wrapped_header_line_is_placed_under_its_columns() -> None:
    """Tarang's balance sheet: the table model gives "31 March 2025 | 31 March 2024" as a row of
    two cells under a four-column header; their boxes put them under the two "As at" columns."""
    found = liteparse_run.read_pages(recorded("tarang_balance_sheet"), [3])

    (table,) = found[3].tables
    assert [c.text for c in table.grid[0]] == ["Particulars", "Note", "As at", "As at"]
    assert [c.text for c in table.grid[1]] == ["", "", "31 March 2025", "31 March 2024"]
    assert any("Balance Sheet" in text for text in found[3].texts)
    values = read_tables(context(3), [table], 4)
    equity = [(v.period, v.rupees, v.note) for v in values if v.label == "(a) Equity share capital"]
    assert equity == [("FY25", Decimal("30000000.00"), "2"), ("FY24", Decimal("30000000.00"), "2")]


def test_a_scope_word_over_two_columns_stays_where_liteparse_put_it() -> None:
    """Standalone and consolidated side by side: LiteParse puts each scope word in one column. The
    adapter adds no layout rule of its own, so the two half-year columns have no scope and fin's
    reader refuses the page (E202): it stays unread, for a Claude reading."""
    found = liteparse_run.read_pages(recorded("side_by_side"), [2])

    (table,) = found[2].tables
    assert [c.text for c in table.grid[0]] == ["", "", "Standalone", "", "Consolidated"]
    ctx2 = context(2, results=True)
    with pytest.raises(PeriodsNotDetected, match=r"page 2 \(LiteParse\): two columns hold"):
        read_tables(ctx2, [table], 4)


def box(left: float, right: float) -> tuple[float, float, float, float]:
    return (left, 0.0, right, 9.0)


def test_a_short_row_cell_that_fits_no_column_is_e204() -> None:
    grid = [
        [Cell("Particulars", bbox=box(0, 50)), Cell("2025", bbox=box(100, 130))],
        [Cell("Sales", bbox=box(0, 40)), Cell("10.00", bbox=box(100, 130))],
        [Cell("stray", bbox=box(200, 230))],
    ]
    with pytest.raises(ParseError, match="under no column"):
        liteparse_run.square(grid)


def test_a_short_row_cell_covering_half_of_no_column_is_e204() -> None:
    """A cell straddling the gap between two columns is not guessed into either."""
    grid = [
        [Cell("Particulars", bbox=box(0, 50)), Cell("2025", bbox=box(100, 130))],
        [Cell("Sales", bbox=box(0, 40)), Cell("10.00", bbox=box(100, 130))],
        [Cell("31 March", bbox=box(40, 110))],
    ]
    with pytest.raises(ParseError, match="under no column"):
        liteparse_run.square(grid)


def test_a_short_row_cell_without_a_box_is_e204() -> None:
    grid = [[Cell("a", bbox=box(0, 10)), Cell("b", bbox=box(20, 30))], [Cell("31 March 2025")]]
    with pytest.raises(ParseError, match="has no position"):
        liteparse_run.square(grid)


def test_a_page_whose_table_cannot_be_squared_is_unread_with_the_reason() -> None:
    def cell(text: str, x: float | None) -> SimpleNamespace:
        rect = None if x is None else SimpleNamespace(x=x, y=0.0, width=10.0, height=9.0)
        return SimpleNamespace(text=text, bbox=rect)

    block = SimpleNamespace(
        kind="merged_table",
        text="",
        bbox=None,
        header=[],
        rows=[[cell("Particulars", 0), cell("2025", 100)], [cell("2024", None)]],
    )
    page = SimpleNamespace(page_num=1, blocks=[block])

    found = liteparse_run.read_pages([page], [9])

    assert found[9].tables == []
    assert found[9].error is not None
    assert found[9].error.code == "E204"
    assert found[9].error.message.startswith("page 9: table cell '2024' has no position")


def test_full_rows_are_kept_as_they_are() -> None:
    grid = [[Cell("a"), Cell("1.00")], [Cell("b"), Cell("2.00")]]
    assert liteparse_run.square(grid) == grid
