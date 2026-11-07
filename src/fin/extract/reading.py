"""Reading files: a document's figures as Claude read them from its pages (as images) or cells.

fin's own extraction is the primary path. Where it cannot read a document or a statement (a scanned
PDF, E104; no period header, E202; a skipped page), the user may ask Claude Code to read those
pages and write a reading file. `fin ingest <company> <file> --reading <reading.csv>` loads it for
the statements fin could not read, and `fin verify` compares a second, independent reading with
what is loaded. Readings live in the data folder (`review/<company>/`), never in the repo.

A reading is a UTF-8 CSV with a header row and one row per figure (one statement line in one
period column). Columns, in any order:

- `statement`: balance_sheet, profit_and_loss or cash_flow.
- `scope`: standalone or consolidated, as the page title says.
- `period_end`: the column's date, YYYY-MM-DD (2025-03-31).
- `period_months`: the column's length, 3, 6, 9 or 12. Leave it empty for a balance sheet (a
  point in time). The period name (FY25, FY26-Q1, FY26-H1) follows from both, as in
  `fin.extract.periods`.
- `unit`: the page's unit line as printed, e.g. `(₹ in Lakhs)` (`fin.extract.units`).
- `section`: the heading the line sits under, as printed ("Current liabilities"); may be empty.
- `label`: the line's label as printed.
- `value_as_printed`: the figure exactly as printed: `1,234.56`, `(56.67)` for a negative, `-`
  for nil. Never computed or inferred.
- `source`: where in the ORIGINAL document: a page number (`12`, `p12` or `page 12`) or
  `Sheet!B7`.

Errors: E205 for a file that cannot be used (missing columns, unknown statement or scope, bad
date, figure or source, no rows), E203 for a unit line no unit can be read from, E202 for a
period that does not line up with the fiscal year. Each message names the CSV line.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from fin.audit.errors import ReadingInvalid, UnitNotDetected
from fin.config import SCOPES
from fin.extract.periods import STATEMENTS, fiscal_year_of, period_of, resolve_months
from fin.extract.units import parse_figure, to_rupees, unit_in

COLUMNS = (
    "statement",
    "scope",
    "period_end",
    "period_months",
    "unit",
    "section",
    "label",
    "value_as_printed",
    "source",
)
_PAGE = re.compile(r"^(?:p(?:age)?\.?\s*)?(\d{1,5})$", re.I)
_CELL = re.compile(r"^(.+)!\$?([A-Z]{1,3})\$?(\d{1,7})$")


@dataclass(frozen=True)
class ReadingLine:
    """One figure of a reading."""

    line: int  # line number in the CSV (the header is line 1)
    statement: str
    scope: str
    section: str
    label: str
    raw: str  # as printed
    unit_text: str  # the unit line as printed
    unit: str  # "lakh", "crore", ...
    period_end: date
    period_months: int
    fiscal_year: str
    period: str
    rupees: Decimal
    page: int | None
    sheet: str | None
    cell: str | None

    @property
    def source(self) -> str:
        return f"page {self.page}" if self.page is not None else f"{self.sheet}!{self.cell}"


def printed_value(text: str, where: str) -> Decimal:
    """A figure as printed -> Decimal in document units (`fin.extract.units.parse_figure`).
    Raises E205 for anything else."""
    value = parse_figure(text)
    if value is None:
        raise ReadingInvalid(f"{where}: {text.strip()!r} is not a figure as printed")
    return value


def _source(text: str, where: str) -> tuple[int | None, str | None, str | None]:
    """(page, sheet, cell) from 'page 12' / 'p12' / '12' or 'Sheet!B7'."""
    text = text.strip()
    page = _PAGE.match(text)
    if page:
        return int(page.group(1)), None, None
    cell = _CELL.match(text)
    if cell:
        return None, cell.group(1).strip("'"), f"{cell.group(2)}{cell.group(3)}"
    raise ReadingInvalid(f"{where}: source {text!r} is not a page number or Sheet!Cell")


def read_reading(path: Path, start_month: int) -> list[ReadingLine]:
    """Parse and check a reading file. Raises E205 / E203 / E202 (see the module docstring)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            header = rows[0].keys() if rows else []
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ReadingInvalid(f"{path.name}: cannot read the reading file ({exc})") from exc
    if not rows:
        raise ReadingInvalid(f"{path.name}: the reading has no lines")
    missing = [column for column in COLUMNS if column not in header]
    if missing:
        raise ReadingInvalid(f"{path.name}: missing columns {', '.join(missing)}")
    return [_line(path.name, n, row, start_month) for n, row in enumerate(rows, start=2)]


def _line(name: str, n: int, row: dict[str, str | None], start_month: int) -> ReadingLine:
    where = f"{name} line {n}"
    cells = {column: (row.get(column) or "").strip() for column in COLUMNS}
    statement, scope = cells["statement"], cells["scope"]
    if statement not in STATEMENTS:
        raise ReadingInvalid(f"{where}: statement {statement!r} is not one of {STATEMENTS}")
    if scope not in SCOPES:
        raise ReadingInvalid(f"{where}: scope {scope!r} is not one of {SCOPES}")
    if not cells["label"]:
        raise ReadingInvalid(f"{where}: no label")
    try:
        period_end = date.fromisoformat(cells["period_end"])
    except ValueError as exc:
        text = cells["period_end"]
        raise ReadingInvalid(f"{where}: period_end {text!r} is not YYYY-MM-DD") from exc
    months_text = cells["period_months"]
    if months_text and months_text not in ("3", "6", "9", "12"):
        raise ReadingInvalid(f"{where}: period_months {months_text!r} is not 3, 6, 9 or 12")
    months = resolve_months(
        statement, period_end, int(months_text) if months_text else None, start_month, where=where
    )
    unit = unit_in(cells["unit"])
    if unit is None:
        raise UnitNotDetected(f"{where}: no unit in {cells['unit']!r} (such as '₹ in Lakhs')")
    value = printed_value(cells["value_as_printed"], where)
    page, sheet, cell = _source(cells["source"], where)
    return ReadingLine(
        line=n,
        statement=statement,
        scope=scope,
        section=cells["section"],
        label=cells["label"],
        raw=cells["value_as_printed"],
        unit_text=cells["unit"],
        unit=unit,
        period_end=period_end,
        period_months=months,
        fiscal_year=fiscal_year_of(period_end, start_month),
        period=period_of(period_end, months, start_month, where=where),
        rupees=to_rupees(value, unit),
        page=page,
        sheet=sheet,
        cell=cell,
    )
