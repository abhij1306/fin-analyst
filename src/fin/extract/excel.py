"""Extract statement values from an Excel workbook (.xlsx), with sheet and cell.

Each sheet is read top to bottom:

1. Rows above the header give the sheet's title text: the statement (balance sheet, profit and
   loss, cash flow) and the unit line ("₹ in Lakhs").
   A sheet whose title names no statement (a cover page, notes) is skipped.
2. The header is the first row with a period cell: a date ("31.03.2025", "31-Mar-2025",
   "31 March 2025", an Excel date) alone or after "As at" / "Year ended" / "Quarter ended" /
   "Half year ended" / "Nine months ended". Each such cell starts a value column, so any number of
   periods side by side works. A column's period length comes from the nearest heading naming one,
   from its period cell up: the words before the date, or a cell above that only names a length
   ("Quarter ended") (`fin.extract.periods`); ingest names the period ("FY25", "FY26-Q1"). Its
   scope comes from the nearest "Standalone" / "Consolidated" above it, else from the one scope
   the sheet names. A heading covers the columns it is merged over, or, typed once and not
   merged, the blank cells right of it in its row. A trailing "(Unaudited)" / "(Audited)" /
   "(Reviewed)" is ignored, and a row of such words under the dates is not a value row. E202 when
   a flow column names no length on a sheet with quarter, half-year or nine-month columns, or
   names no scope on a sheet naming both.
3. Below the header, a row with a label and numbers in the period columns is a value row; a row
   with a label and no numbers is a section heading for the rows under it.

Formula cells are read through the values Excel (or LibreOffice) cached when the file was saved. A
formula with no cached value raises E204: open the file in Excel, save it and ingest it again.
"""

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from fin.audit.errors import (
    FileEncrypted,
    FileUnreadable,
    NoTablesFound,
    ParseError,
    PeriodsNotDetected,
    UnitNotDetected,
)
from fin.extract.periods import MONTHS, PHRASE, months_in
from fin.extract.units import parse_figure, to_rupees, unit_in

# An encrypted .xlsx is not a zip file but an OLE compound file with this signature.
_OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")

_PERIOD_PREFIX = (
    r"(?:(?:as\s+(?:at|on)|(?:for\s+the\s+)?(?:year|period|quarter|half[\s-]*year"
    r"|(?:three|six|nine|twelve)\s+months?)\s+(?:ended|ending))\s+)?"
)
_PERIOD_PATTERNS = (
    # 31.03.2025, 31/03/2025, 31-03-2025
    (re.compile(rf"^{_PERIOD_PREFIX}(\d{{1,2}})[./-](\d{{1,2}})[./-](\d{{4}})$"), "dmy"),
    # 31-Mar-2025, 31 March 2025, 31st March, 2025
    (
        re.compile(
            rf"^{_PERIOD_PREFIX}(\d{{1,2}})(?:st|nd|rd|th)?[\s-]+([a-z]{{3,9}})[\s,-]+(\d{{4}})$"
        ),
        "dMy",
    ),
    # March 31, 2025
    (re.compile(rf"^{_PERIOD_PREFIX}([a-z]{{3,9}})\s+(\d{{1,2}}),?\s+(\d{{4}})$"), "Mdy"),
)
_STATEMENTS = (
    ("cash_flow", re.compile(r"cash\s*flow|\bcf\b")),
    ("balance_sheet", re.compile(r"balance\s*sheet|\bbs\b")),
    ("profit_and_loss", re.compile(r"profit|p\s*&\s*l|\bloss\b|income\s+statement")),
)
_SCOPE_WORD = re.compile(r"\b(standalone|consolidated)\b")  # in lower-case text
# An audit status after a heading or alone: "Quarter ended (Unaudited)", "(Audited)".
_AUDIT_STATUS = re.compile(r"\s*\(\s*(?:audited|unaudited|reviewed)\s*\)$")  # in lower-case text


@dataclass(frozen=True)
class ExcelValue:
    sheet: str
    cell: str  # e.g. "C7"
    statement: str  # balance_sheet | profit_and_loss | cash_flow
    scope: str | None  # standalone | consolidated; None when the sheet does not say
    section: str  # nearest heading row above, as written ("" if none)
    label: str  # as written, surrounding spaces removed
    raw: str  # the cell value as text, e.g. "13409.29"
    unit_text: str  # the unit line as written, e.g. "(₹ in Lakhs)"
    unit: str  # lakh, crore, ...
    period_end: date
    months: int | None  # period length the header cell names (fin.extract.periods.months_in)
    rupees: Decimal


@dataclass(frozen=True)
class ExcelExtraction:
    values: list[ExcelValue]
    skipped_sheets: list[str]  # sheets with no statement title


def extract_workbook(path: Path) -> ExcelExtraction:
    """Extract every statement value. Raises E101, E102, E201, E202, E203 or E204."""
    values_book = _open(path, data_only=True)
    formula_book = _open(path, data_only=False)
    values: list[ExcelValue] = []
    skipped: list[str] = []
    for sheet in values_book.worksheets:
        found = _sheet_values(path, sheet, formula_book[sheet.title])
        if found is None:
            skipped.append(sheet.title)
        else:
            values.extend(found)
    if not values:
        raise NoTablesFound(f"{path.name}: no balance sheet, profit and loss or cash flow values")
    return ExcelExtraction(values, skipped)


def _open(path: Path, *, data_only: bool) -> Workbook:
    try:
        with path.open("rb") as handle:
            if handle.read(8) == _OLE_SIGNATURE:
                raise FileEncrypted(f"{path.name}: the workbook is password protected")
        return load_workbook(path, data_only=data_only)
    except (OSError, zipfile.BadZipFile, InvalidFileException, KeyError, ValueError) as exc:
        raise FileUnreadable(f"{path.name}: not a readable .xlsx workbook ({exc})") from exc


def parse_period(value: Any) -> date | None:
    """The period end a header cell names, or None if it is not a period header."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text_of(value)
    for pattern, order in _PERIOD_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        a, b, c = match.groups()
        if order == "dmy":
            day, month, year = int(a), int(b), int(c)
        elif order == "dMy":
            day, month, year = int(a), MONTHS.get(b[:3], 0), int(c)
        else:
            day, month, year = int(b), MONTHS.get(a[:3], 0), int(c)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def statement_in(text: str) -> str | None:
    """The statement a sheet holds, from its name and title rows (lower case)."""
    for statement, pattern in _STATEMENTS:
        if pattern.search(text):
            return statement
    return None


def parse_number(value: Any, where: str) -> tuple[str, Decimal]:
    """A value cell as (raw text, Decimal). Text is read as a printed figure
    (`fin.extract.units.parse_figure`: "1,234.50", "(56.67)", a dash or "nil" for nil). Raises
    E204 for anything else."""
    if isinstance(value, bool):
        raise ParseError(f"{where}: TRUE/FALSE where a number was expected")
    if isinstance(value, int | float):
        raw = repr(value) if isinstance(value, float) else str(value)
        return raw, Decimal(raw)
    text = str(value).strip()
    number = parse_figure(text)
    if number is None:
        raise ParseError(f"{where}: {text!r} is not a number")
    return text, number


@dataclass
class _Header:
    """A sheet's header row: its row number, and per value column the period end."""

    row: int
    periods: dict[int, date]  # column index -> period end


def _find_header(sheet: Worksheet) -> tuple[list[str], _Header | None]:
    """The sheet's title text (sheet name, then the text of every row above the header) and its
    header row (None when no row holds a period cell)."""
    title_parts = [sheet.title]
    for row in sheet.iter_rows():
        periods = {c.column: p for c in row if (p := parse_period(c.value)) is not None}
        if periods:
            return title_parts, _Header(row[0].row, periods)
        title_parts.extend(str(c.value) for c in row if c.value is not None)
    return title_parts, None


def _value_at(sheet: Worksheet, row: int, column: int) -> Any:
    """A cell's value; a merged cell's value in every cell it spans."""
    for span in sheet.merged_cells.ranges:
        if span.min_row <= row <= span.max_row and span.min_col <= column <= span.max_col:
            return sheet.cell(span.min_row, span.min_col).value
    return sheet.cell(row, column).value


def _text_of(value: Any) -> str:
    """A header cell's text: lower case, spaces collapsed, a trailing "(Unaudited)" / "(Audited)"
    / "(Reviewed)" removed; '' for a blank or non-text cell."""
    if not isinstance(value, str):
        return ""
    return _AUDIT_STATUS.sub("", " ".join(value.lower().split()))


def _is_heading(text: str) -> bool:
    """Whether a header cell names a period length or exactly one scope."""
    return _length_named(text) is not None or len(set(_SCOPE_WORD.findall(text))) == 1


def _headings(sheet: Worksheet, header: _Header, column: int) -> list[str]:
    """A value column's header texts, nearest first: its period cell, then each cell above it. A
    blank cell above the header takes the heading left of it in its row (a group heading typed
    once, over the value columns, not merged)."""
    first = min(header.periods)
    texts = [_text_of(_value_at(sheet, header.row, column))]
    for row in range(header.row - 1, 0, -1):
        text = _text_of(_value_at(sheet, row, column))
        if not text and column > first:
            left = next(
                (
                    t
                    for c in range(column - 1, first - 1, -1)
                    if (t := _text_of(sheet.cell(row, c).value))
                ),
                "",
            )
            text = left if _is_heading(left) else ""
        texts.append(text)
    return [t for t in texts if t]


def _length_named(text: str) -> int | None:
    """The period length a heading names: a period cell ("Quarter ended 31.12.2025") or a cell
    that only names a length ("Six months ended"). Titles are not headings."""
    if parse_period(text) is not None or PHRASE.match(text):
        return months_in(text)
    return None


def _length_of(headings: list[str]) -> int | None:
    """The period length the nearest heading naming one gives."""
    return next((m for t in headings if (m := _length_named(t)) is not None), None)


def _scope_of(headings: list[str]) -> str | None:
    """The scope the nearest heading naming exactly one gives ("Standalone", "Consolidated")."""
    for text in headings:
        words = set(_SCOPE_WORD.findall(text))
        if len(words) == 1:
            return words.pop()
    return None


def _columns(
    sheet: Worksheet, header: _Header, statement: str, title: str, where: str
) -> dict[int, tuple[int | None, str | None]]:
    """Per value column, its period length (None when no heading names one) and scope. Raises
    E202 when a flow column names no length on a sheet with quarter, half-year or nine-month
    columns, or a column has no scope heading on a sheet that names both scopes."""
    headings = {c: _headings(sheet, header, c) for c in header.periods}
    lengths = {c: _length_of(h) for c, h in headings.items()}
    unnamed = [c for c, months in lengths.items() if months is None]
    sheet_text = " ".join([title, *(t for texts in headings.values() for t in texts)])
    if unnamed and statement != "balance_sheet" and months_in(sheet_text) in (3, 6, 9):
        raise PeriodsNotDetected(
            f"{where} column {get_column_letter(unnamed[0])}: no heading says whether it is a "
            "quarter, half year, nine months or year, and the sheet has interim columns"
        )
    scopes = {c: _scope_of(h) for c, h in headings.items()}
    unscoped = [c for c, scope in scopes.items() if scope is None]
    named = set(_SCOPE_WORD.findall(title)) | {s for s in scopes.values() if s}
    if unscoped and len(named) > 1:
        raise PeriodsNotDetected(
            f"{where} column {get_column_letter(unscoped[0])}: no 'Standalone' or "
            "'Consolidated' heading above it, and the sheet names both"
        )
    fallback = next(iter(named), None)
    return {c: (lengths[c], scopes[c] or fallback) for c in header.periods}


def _label_of(row: tuple[Any, ...], first_value_column: int) -> str:
    """A row's label: the first text cell left of the value columns, or ''."""
    return next(
        (
            str(c.value).strip()
            for c in row[: first_value_column - 1]
            if isinstance(c.value, str) and c.value.strip()
        ),
        "",
    )


def _filled_cells(
    row: tuple[Any, ...], periods: dict[int, date], formulas: Worksheet, where: str
) -> list[Any]:
    """The row's value-column cells that hold a value (an audit status such as "(Unaudited)" is
    not one). Raises E204 for a formula with no saved value."""
    cells = [row[column - 1] for column in periods if column <= len(row)]
    for cell in cells:
        if cell.value is None and str(formulas[cell.coordinate].value).startswith("="):
            raise ParseError(
                f"{where} cell {cell.coordinate}: formula has no saved value; open the file "
                "in Excel, save it and ingest it again"
            )
    return [
        c
        for c in cells
        if c.value is not None and (_text_of(c.value) or not isinstance(c.value, str))
    ]


def _sheet_values(path: Path, sheet: Worksheet, formulas: Worksheet) -> list[ExcelValue] | None:
    """Values of one sheet, or None if it holds no statement."""
    title_parts, header = _find_header(sheet)
    title = " ".join(title_parts).lower()
    statement = statement_in(title)
    if statement is None:
        return None
    where = f"{path.name} sheet {sheet.title!r}"
    if header is None:
        raise PeriodsNotDetected(f"{where}: no header row with period dates such as 31.03.2025")
    units = [(text.strip(), unit) for text in title_parts[1:] if (unit := unit_in(text))]
    if not units:
        raise UnitNotDetected(f"{where}: no unit line such as '(₹ in Lakhs)' above the header")
    unit_text, unit = units[0]
    columns = _columns(sheet, header, statement, title, where)
    first_value_column = min(header.periods)

    found: list[ExcelValue] = []
    section = ""
    for row in sheet.iter_rows(min_row=header.row + 1):
        label = _label_of(row, first_value_column)
        filled = _filled_cells(row, header.periods, formulas, where)
        if not filled:
            if label:
                section = label
            continue
        if not label:
            raise ParseError(f"{where} row {row[0].row}: values with no label")
        for cell in filled:
            raw, number = parse_number(cell.value, f"{where} cell {cell.coordinate}")
            found.append(
                ExcelValue(
                    sheet=sheet.title,
                    cell=cell.coordinate,
                    statement=statement,
                    scope=columns[cell.column][1],
                    section=section,
                    label=label,
                    raw=raw,
                    unit_text=unit_text,
                    unit=unit,
                    period_end=header.periods[cell.column],
                    months=columns[cell.column][0],
                    rupees=to_rupees(number, unit),
                )
            )
    return found
