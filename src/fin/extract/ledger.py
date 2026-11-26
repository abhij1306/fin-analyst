"""Read a tabular source file (a Tally Trial Balance or Ledger Vouchers export) with a profile.

`read_table` finds, on each sheet, the column header the profile names (`fin.extract.profiles`),
reads the period covered from the header lines above it, and returns every non-blank row below it
with its sheet and row number, its cells by field, and the name cell's bold and indent (a trial
balance tells group rows from ledger rows by them). What the rows mean is `fin.diligence.tally`'s.

The first profile of the kind (or the one named) whose header is found is used. E807 when none
matches: the message lists the title and column header lines (never data rows), so Claude can write
a profile. E808 when no header line gives the period, or sheets give different periods.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from fin.audit.errors import LayoutUnknown, ParseError, SourcePeriodInvalid
from fin.extract.excel import open_workbook, parse_number
from fin.extract.profiles import Profile, load_profile, norm, profiles_for
from fin.extract.units import parse_figure

HEADER_SEARCH_ROWS = 60  # the column header is looked for in this many rows from the top
SHOWN_ROWS = 12  # rows listed when no profile matches
DATE_TEXT = re.compile(r"\d{1,4}[-/. ]\w{1,9}[-/. ]\d{2,4}")  # a date written as text: 12/05/2024


@dataclass(frozen=True)
class SheetRow:
    sheet: str
    row: int
    cells: dict[str, Any]  # field -> cell value
    bold: bool  # the first field's cell (name, or the ledger block field)
    indent: float


@dataclass(frozen=True)
class Table:
    profile: Profile
    start: date
    end: date
    rows: list[SheetRow]
    lines: tuple[str, ...] = ()  # text of the rows above the column header, a row's cells joined


def _merged(sheet: Worksheet) -> dict[tuple[int, int], Any]:
    """(row, column) -> value of every cell inside a merged range near the top of the sheet."""
    filled = {}
    for span in sheet.merged_cells.ranges:
        if span.min_row > HEADER_SEARCH_ROWS + 5:
            continue
        value = sheet.cell(span.min_row, span.min_col).value
        for row in range(span.min_row, span.max_row + 1):
            for column in range(span.min_col, span.max_col + 1):
                filled[(row, column)] = value
    return filled


def _header(sheet: Worksheet, profile: Profile) -> tuple[int, dict[int, str]] | None:
    """(first data row, column -> field) where the profile's header is found, else None."""
    if not profile.columns:  # a registers profile: see fin.extract.registers
        return None
    merged = _merged(sheet)
    width = min(sheet.max_column, 60)
    for top in range(1, min(sheet.max_row, HEADER_SEARCH_ROWS) + 1):
        found = _columns_at(sheet, merged, top, profile, width)
        if len(found) == len(set(profile.columns.values())):
            return top + profile.header_rows, found
    return None


def _header_text(
    sheet: Worksheet, merged: dict[tuple[int, int], Any], top: int, column: int, rows: int
) -> str:
    """The header text of a column over `rows` rows from `top`, repeated lines once."""
    parts: list[str] = []
    for row in range(top, top + rows):
        text = norm(merged.get((row, column), sheet.cell(row, column).value))
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " ".join(parts)


def _columns_at(
    sheet: Worksheet, merged: dict[tuple[int, int], Any], top: int, profile: Profile, width: int
) -> dict[int, str]:
    """column -> field for the profile's headers found in the rows from `top`."""
    found: dict[int, str] = {}
    for column in range(1, width + 1):
        text = _header_text(sheet, merged, top, column, profile.header_rows)
        field = profile.columns.get(text)
        if field is not None and field not in found.values():
            found[column] = field
    return found


def parse_date(value: Any, profile: Profile) -> date | None:
    """A date cell (an Excel date, or text in the profile's date_format), else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), profile.date_format).date()
        except ValueError:
            return None
    return None


def amount(value: Any, profile: Profile, where: str) -> Decimal:
    """An amount cell in rupees: blank is 0. E204 when it is not a number."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal("0.00")
    _, number = parse_number(value, where)
    return (number * profile.unit).quantize(Decimal("0.01"))


def _period(sheet: Worksheet, first_data_row: int, profile: Profile) -> tuple[date, date] | None:
    """The period a header line above the column header names ("1-Apr-2024 to 31-Mar-2025")."""
    pattern = profile.period
    if pattern is None:
        return None
    for row in sheet.iter_rows(max_row=max(first_data_row - profile.header_rows - 1, 0)):
        for cell in row:
            if cell.value is None:
                continue
            match = pattern.search(str(cell.value))
            found = _period_of(match, pattern, profile) if match is not None else None
            if found is not None:
                return found
    return None


def _period_of(
    match: re.Match[str], pattern: re.Pattern[str], profile: Profile
) -> tuple[date, date] | None:
    if "date" in pattern.groupindex:  # a payroll snapshot: one date
        day = parse_date(match.group("date"), profile)
        return (day, day) if day is not None else None
    start, end = (parse_date(match.group(g), profile) for g in ("start", "end"))
    return (start, end) if start is not None and end is not None else None


def cell_text(value: Any) -> str:
    """A cell as text for header-line patterns: an integer as digits, a float with two decimals
    (an opening balance), anything else with spaces collapsed; '' for blank."""
    if value is None or isinstance(value, bool):
        return "" if value is None else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return " ".join(str(value).split())


def _lines(sheet: Worksheet, first_data_row: int, profile: Profile) -> list[str]:
    """The text of each non-blank row above the column header (a bank statement's account number,
    currency and opening balance), its cells joined with spaces."""
    out = []
    for row in sheet.iter_rows(max_row=max(first_data_row - profile.header_rows - 1, 0)):
        cells = [cell_text(c.value) for c in row]
        if any(cells):
            out.append(" ".join(c for c in cells if c))
    return out


def _rows(sheet: Worksheet, first: int, columns: dict[int, str]) -> list[SheetRow]:
    lead = min(columns)
    out = []
    for cells in sheet.iter_rows(min_row=first):
        values = {field: cells[c - 1].value for c, field in columns.items() if c <= len(cells)}
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values.values()):
            continue
        cell = cells[lead - 1]
        out.append(
            SheetRow(
                sheet.title,
                cell.row,
                values,
                bool(cell.font and cell.font.b),
                float(cell.alignment.indent or 0) if cell.alignment else 0.0,
            )
        )
    return out


def is_data(value: Any) -> bool:
    """A number, a date, or a figure or date written as text: a cell only data rows hold."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | date):  # datetime is a date
        return True
    text = str(value).strip()
    return parse_figure(text) is not None or DATE_TEXT.fullmatch(text) is not None


def _top_rows(sheet: Worksheet) -> list[str]:
    """The title and column header lines: the rows above the first row holding a number, a date or
    a figure, ending before a one-cell row below a wider one (a ledger block's heading). Data
    rows are never listed."""
    lines, widest = [], 0
    for row in sheet.iter_rows(max_row=HEADER_SEARCH_ROWS):
        values = [c.value for c in row if c.value is not None and str(c.value).strip()]
        if any(is_data(v) for v in values) or (len(values) == 1 and widest > 1):
            break
        widest = max(widest, len(values))
        if values:
            texts = (" ".join(str(v).split()) for v in values)
            lines.append(f"row {row[0].row}: {' | '.join(texts)}")
    return lines[:SHOWN_ROWS]


def read_table(
    path: Path,
    kind: str,
    profile_name: str | None = None,
) -> Table:
    """The rows of `path` read with the named profile, or the first profile of `kind` whose
    header is found. E101/E102 (unreadable), E807, E808."""
    if profile_name is not None:
        candidates = [load_profile(profile_name)]
        if candidates[0].kind != kind:
            raise LayoutUnknown(
                f"column profile {profile_name!r} reads {candidates[0].kind}, not {kind}"
            )
    else:
        candidates = profiles_for(kind)
    book = open_workbook(path, data_only=True)
    for profile in candidates:
        headers = [(s, h) for s in book.worksheets if (h := _header(s, profile)) is not None]
        if headers:
            return _table(path, profile, headers)
    names = ", ".join(p.name for p in candidates) or "none"
    shown = "; ".join(f"sheet {s.title!r}: " + " / ".join(_top_rows(s)) for s in book.worksheets)
    raise LayoutUnknown(
        f"{path.name}: no {kind} column profile matches its headers (tried: {names}). "
        f"Title and header lines: {shown or 'none'}. Write a profile in templates/profiles/ "
        "(docs/development.md) and ingest again with --profile"
    )


def _table(
    path: Path, profile: Profile, headers: list[tuple[Worksheet, tuple[int, dict[int, str]]]]
) -> Table:
    periods, rows, lines = set(), [], []
    for sheet, (first, columns) in headers:
        period = _period(sheet, first, profile)
        if period is None:
            raise SourcePeriodInvalid(
                f"{path.name} sheet {sheet.title!r}: no header line above the column header gives "
                f"the period covered (profile {profile.name}, pattern "
                f"{profile.period and profile.period.pattern!r})"
            )
        periods.add(period)
        rows.extend(_rows(sheet, first, columns))
        lines.extend(_lines(sheet, first, profile))
    if len(periods) > 1:
        found = ", ".join(f"{a} to {b}" for a, b in sorted(periods))
        raise SourcePeriodInvalid(f"{path.name}: its sheets cover different periods ({found})")
    ((start, end),) = periods
    if not rows:
        raise ParseError(f"{path.name}: no rows below the column header")
    return Table(profile, start, end, rows, tuple(lines))
