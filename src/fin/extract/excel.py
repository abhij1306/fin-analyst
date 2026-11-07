"""Extract statement values from an Excel workbook (.xlsx), with sheet and cell.

Each sheet is read top to bottom:

1. Rows above the header give the sheet's title text: the statement (balance sheet, profit and
   loss, cash flow), the scope (standalone or consolidated) and the unit line ("₹ in Lakhs").
   A sheet whose title names no statement (a cover page, notes) is skipped.
2. The header is the first row with a period cell: a date ("31.03.2025", "31-Mar-2025",
   "31 March 2025", an Excel date) alone or after "As at" / "Year ended" / "Quarter ended" /
   "Half year ended" / "Nine months ended". Each such cell starts a value column, so any number of
   periods side by side works. The words before the date give the period length
   (`fin.extract.periods`); ingest names the period ("FY25", "FY26-Q1").
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
from fin.extract.periods import MONTHS, months_in
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
    if not isinstance(value, str):
        return None
    text = " ".join(value.lower().split())
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


def scope_in(text: str) -> str | None:
    if "consolidated" in text:
        return "consolidated"
    if "standalone" in text:
        return "standalone"
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


def _sheet_values(path: Path, sheet: Worksheet, formulas: Worksheet) -> list[ExcelValue] | None:
    """Values of one sheet, or None if it holds no statement."""
    title_parts = [sheet.title]
    header_row: int | None = None
    periods: dict[int, date] = {}  # column index -> period end
    lengths: dict[int, int | None] = {}  # column index -> period length its header names
    for row in sheet.iter_rows():
        periods = {c.column: p for c in row if (p := parse_period(c.value)) is not None}
        if periods:
            lengths = {
                c.column: months_in(c.value) if isinstance(c.value, str) else None
                for c in row
                if c.column in periods
            }
            header_row = row[0].row
            break
        title_parts.extend(str(c.value) for c in row if c.value is not None)
    title = " ".join(title_parts).lower()
    statement = statement_in(title)
    if statement is None:
        return None
    where = f"{path.name} sheet {sheet.title!r}"
    if header_row is None:
        raise PeriodsNotDetected(f"{where}: no header row with period dates such as 31.03.2025")
    units = [(text.strip(), unit) for text in title_parts[1:] if (unit := unit_in(text))]
    if not units:
        raise UnitNotDetected(f"{where}: no unit line such as '(₹ in Lakhs)' above the header")
    unit_text, unit = units[0]
    scope = scope_in(title)
    first_value_column = min(periods)

    found: list[ExcelValue] = []
    section = ""
    for row in sheet.iter_rows(min_row=header_row + 1):
        label = next(
            (
                str(c.value).strip()
                for c in row[: first_value_column - 1]
                if isinstance(c.value, str) and c.value.strip()
            ),
            "",
        )
        cells = [row[column - 1] for column in periods if column <= len(row)]
        for cell in cells:
            if cell.value is None and str(formulas[cell.coordinate].value).startswith("="):
                raise ParseError(
                    f"{where} cell {cell.coordinate}: formula has no saved value; open the file "
                    "in Excel, save it and ingest it again"
                )
        filled = [c for c in cells if c.value is not None and str(c.value).strip() != ""]
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
                    scope=scope,
                    section=section,
                    label=label,
                    raw=raw,
                    unit_text=unit_text,
                    unit=unit,
                    period_end=periods[cell.column],
                    months=lengths[cell.column],
                    rupees=to_rupees(number, unit),
                )
            )
    return found
