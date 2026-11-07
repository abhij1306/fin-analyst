"""Turn the tables Docling found on a statement page into `PdfValue`s, with fin's own rules.

Docling (`fin.extract.docling_run`) reads the statement pages pdfplumber skipped. It gives each
table as a grid of cells (a cell spanning columns repeats in each), with Docling's column-header
flag and a bounding box. This module reads such a grid the way `fin.extract.pdf` reads a page, with
the same helpers and guards, and needs no Docling itself:

1. Header: the rows at the top up to the first row (not flagged as a column header) with a figure
   right of its first column. Each column's header text, top to bottom, is its stack.
2. A column whose stack names a period end (`period_end_in`) is a period column; its length comes
   from the stack (`months_in`: "Quarter ended", "Year ended") and its scope from a "Standalone" /
   "Consolidated" over it (a spanning cell), else from the page title. Its period is named by
   `resolve_months` / `period_of`. Two columns with the same period and scope: the page is not read
   (E202), as is a results column that does not name its length.
3. Label: the text of the columns left of the first period column, minus a "Note" column (kept as
   the note reference). A row with no figure is a heading: the section of the rows below it
   (a sub-heading "(a)" is not). A row with figures and no label is an unlabelled subtotal and takes
   the section as its label.
4. Figures: `parse_figure` (nil dashes are 0, brackets are negative). A cell holding digits that is
   not one figure, a figure with an unpaired bracket, or a label holding a figure means the table
   was misread: the page is not read (E204).
5. Unit: from the page's lines (pdfplumber), else from the table's own text (a bracketed "(in
   Lakhs)" counts too: OCR drops the ₹ sign); none: not read (E203).

A table with no period columns (a note, a signature block) is passed over; a page where no table
gives a figure is not read (E201). Every value keeps its cell's bbox in fin's convention (PDF
points from the page's top-left corner), so evidence crops work as for pdfplumber's values.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fin.audit.errors import NoTablesFound, ParseError, PeriodsNotDetected, UnitNotDetected
from fin.extract.pdf import FIGURE_TEXT, SUB_HEADING, PdfValue, period_end_in
from fin.extract.periods import fiscal_year_of, months_in, period_of, resolve_months
from fin.extract.units import parse_figure, to_rupees, unit_in

Box = tuple[float, float, float, float]  # x0, top, x1, bottom: points from the page's top-left

_SCOPE_WORD = re.compile(r"\b(standalone|consolidated)\b", re.I)
_NOTE_HEADER = re.compile(r"^notes?\b\.?(?:\s*no\.?)?$", re.I)
_YEAR = re.compile(r"^(19|20)\d\d$")
_DIGIT = re.compile(r"\d")
_WORDY = re.compile(r"[a-z]{3}", re.I)
# A unit line whose currency sign OCR dropped: "( in Lakhs)" for "(₹ in Lakhs)".
_BARE_UNIT = re.compile(r"\(\s*in\s+(thousand|lakh|lac|million|crore)s?\s*\)", re.I)


@dataclass(frozen=True)
class Cell:
    text: str
    header: bool = False  # Docling flagged it as a column header
    bbox: Box | None = None


Grid = list[list[Cell]]  # rows of cells; a spanning cell repeats in every column it spans


@dataclass(frozen=True)
class Table:
    grid: Grid
    bbox: Box | None = None  # the whole table, when a cell has none


@dataclass(frozen=True)
class PageContext:
    """What the page around the tables says (pdfplumber's lines, or Docling's text when scanned)."""

    page: int  # 1-based, in the original document
    statement: str
    scope: str | None  # from the page title; None when it does not say
    unit: str | None  # from a unit line on the page
    results: bool  # a quarterly or half-year results statement: flow columns must name a length


@dataclass(frozen=True)
class _Column:
    index: int
    period_end: date
    months: int
    period: str
    scope: str | None


def top_left(
    left: float, top: float, right: float, bottom: float, *, bottom_left: bool, height: float
) -> Box:
    """A box in fin's convention (x0, top, x1, bottom from the page's top-left corner). Docling
    gives `top` and `bottom` measured from the page's bottom edge when `bottom_left`."""
    if bottom_left:
        top, bottom = height - top, height - bottom
    return (left, min(top, bottom), right, max(top, bottom))


def read_tables(context: PageContext, tables: list[Table], start_month: int) -> list[PdfValue]:
    """The values of one statement page's tables. Raises E201 (no table gives a figure), E202,
    E203 or E204 (any table misread): the page is then not read at all."""
    unit = context.unit or _unit_of_tables(tables)
    if unit is None:
        raise UnitNotDetected(f"page {context.page}: no unit line such as '₹ in Lakhs'")
    values = [v for table in tables for v in read_table(context, table, unit, start_month)]
    if not values:
        raise NoTablesFound(
            f"page {context.page}: no table with period columns and figures (Docling)"
        )
    return values


def unit_of_text(text: str) -> str | None:
    """The unit `text` names (`unit_in`), or the unit of a bracketed "(in Lakhs)" whose currency
    sign OCR dropped."""
    unit = unit_in(text)
    if unit is not None:
        return unit
    bare = _BARE_UNIT.search(text)
    return bare.group(1).lower() if bare else None


def _unit_of_tables(tables: list[Table]) -> str | None:
    for table in tables:
        for row in table.grid:
            for cell in row:
                unit = unit_of_text(cell.text)
                if unit is not None:
                    return unit
    return None


def read_table(context: PageContext, table: Table, unit: str, start_month: int) -> list[PdfValue]:
    """The values of one table ([] when it has no period columns). Raises E202 or E204."""
    grid = table.grid
    width = max((len(row) for row in grid), default=0)
    end = _header_end(grid)
    stacks = [_stack(grid[:end], c) for c in range(width)]
    columns = _columns(context, stacks, start_month)
    if not columns:
        return []
    first = columns[0].index
    note = next((c for c in range(first) if _NOTE_HEADER.match(stacks[c])), None)
    labels = [c for c in range(first) if c != note]
    values: list[PdfValue] = []
    section = ""
    for row in grid[end:]:
        label = _join(row, labels)
        figures = _figures(row, columns, context.page, table.bbox)
        if not figures:
            if label and _WORDY.search(label) and not SUB_HEADING.match(label):
                section = label
            continue
        note_ref = (_cell(row, note).text.strip() or None) if note is not None else None
        named = (_label(label, context.page) or section, section, note_ref)
        for column, cell, value in figures:
            values.append(_value(context, column, cell, value, named, unit, start_month))
    return values


def _label(label: str, page: int) -> str:
    """A row's label. Raises E204 when it holds a figure (the figures do not line up)."""
    if FIGURE_TEXT.search(label):
        raise ParseError(
            f"page {page}: the label {label[:60]!r} holds a figure; the figures do not line up "
            "with their labels (Docling)"
        )
    return label


def _cell(row: list[Cell], index: int) -> Cell:
    return row[index] if index < len(row) else Cell("")


def _is_figure(text: str) -> bool:
    return bool(text) and parse_figure(text) is not None and not _YEAR.match(text)


def _header_end(grid: Grid) -> int:
    """Index of the first row below the header. The header ends at the first row (not flagged as a
    column header) with a figure right of its first column; above that, rows with text only in the
    first column after the last row with text right of it are headings, not header."""
    end = 0
    for i, row in enumerate(grid):
        header = any(c.header for c in row)
        if not header and any(_is_figure(c.text.strip()) for c in row[1:]):
            break
        if header or any(c.text.strip() for c in row[1:]):
            end = i + 1
    return end


def _stack(rows: Grid, index: int) -> str:
    """A column's header text, top to bottom, a spanning cell's text once."""
    texts: list[str] = []
    for row in rows:
        text = _cell(row, index).text.strip()
        if text and (not texts or texts[-1] != text):
            texts.append(text)
    return " ".join(texts)


def _columns(context: PageContext, stacks: list[str], start_month: int) -> list[_Column]:
    """The period columns, named. Raises E202 when a period cannot be told or two columns hold
    the same period and scope."""
    where = f"page {context.page} (Docling)"
    columns: list[_Column] = []
    for index, stack in enumerate(stacks):
        end = period_end_in(stack)
        if end is None:
            continue
        months = months_in(stack)
        if context.results and context.statement != "balance_sheet" and months is None:
            raise PeriodsNotDetected(
                f"{where}: results statement column ending {end.isoformat()} does not say "
                "whether it is a quarter, half year or year"
            )
        months = resolve_months(context.statement, end, months, start_month, where=where)
        scope = _SCOPE_WORD.search(stack)
        columns.append(
            _Column(
                index,
                end,
                months,
                period_of(end, months, start_month, where=where),
                scope.group(1).lower() if scope else context.scope,
            )
        )
    keys = [(c.period, c.scope) for c in columns]
    if len(set(keys)) < len(keys):
        listed = ", ".join(f"{c.period} {c.scope or 'unscoped'}" for c in columns)
        raise PeriodsNotDetected(f"{where}: two columns hold the same period ({listed})")
    return columns


def _join(row: list[Cell], indexes: list[int]) -> str:
    """The text of a row's label columns, a spanning cell's text once."""
    texts: list[str] = []
    for index in indexes:
        text = " ".join(_cell(row, index).text.split())
        if text and (not texts or texts[-1] != text):
            texts.append(text)
    return " ".join(texts)


def _figures(
    row: list[Cell], columns: list[_Column], page: int, table_box: Box | None
) -> list[tuple[_Column, Cell, Decimal]]:
    """(column, cell, value) for each period column holding a figure. Raises E204 for a cell with
    digits that is not one figure, or a figure with an unpaired bracket."""
    found = []
    for column in columns:
        cell = _cell(row, column.index)
        text = " ".join(cell.text.split())
        if not text:
            continue
        if _DIGIT.search(text) and text.startswith("(") != text.endswith(")"):
            raise ParseError(f"page {page}: figure {text!r} has an unpaired bracket (Docling)")
        value = parse_figure(text)
        if value is None:
            if _DIGIT.search(text):
                raise ParseError(f"page {page}: cell {text[:30]!r} is not one figure (Docling)")
            continue  # words under a period column ("Audited"): not a figure
        found.append((column, Cell(text, cell.header, cell.bbox or table_box), value))
    return found


def _value(
    context: PageContext,
    column: _Column,
    cell: Cell,
    value: Decimal,
    row: tuple[str, str, str | None],
    unit: str,
    start_month: int,
) -> PdfValue:
    label, section, note = row
    return PdfValue(
        label=label,
        fiscal_year=fiscal_year_of(column.period_end, start_month),
        period=column.period,
        months=column.months,
        period_end=column.period_end,
        raw=cell.text,
        value=value,
        rupees=to_rupees(value, unit),
        unit=unit,
        page=context.page,
        bbox=cell.bbox or (0.0, 0.0, 0.0, 0.0),
        statement=context.statement,
        scope=column.scope,
        section=section,
        note=note,
    )
