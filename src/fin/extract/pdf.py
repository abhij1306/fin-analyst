"""Extract statement values from a text PDF annual report, with page and bounding box.

Tables are read from word positions, not ruled lines, so the same code handles tables drawn with
whitespace only and tables with rules. On each statement page (balance sheet, profit and loss,
cash flow, notes):

1. Words are grouped into lines by their vertical position.
2. A line holding two or more period labels ("31 March 2024", "2023-24") is a column header. Each
   period becomes a column, anchored at the right edge of its label (figures are right-aligned).
3. A line below a header whose trailing words are numbers right-aligned under the period columns is
   a value row. The words to their left are the label, plus a note reference when the header has a
   "Note" column. A line without values is a section heading.

Every value keeps its raw text, the parsed `Decimal` in document units, the same in rupees, the
1-based page number and the word's bbox `(x0, top, x1, bottom)` in PDF points from the page's
top-left corner (pdfplumber's convention, so `page.crop(bbox)` shows it).
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import PdfminerException

from fin.audit.errors import (
    FileEncrypted,
    FileUnreadable,
    NoTablesFound,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)

# Page titles that mark a statement page, checked in order against the page's first lines.
STATEMENT_TITLES = (
    ("balance_sheet", re.compile(r"balance sheet", re.I)),
    ("profit_and_loss", re.compile(r"profit (and|&) loss", re.I)),
    ("cash_flow", re.compile(r"cash flows?( statement)?\b", re.I)),
    ("notes", re.compile(r"^notes\b", re.I)),
)
TITLE_LINES = 3  # how many lines from the top of a page can hold its title

_UNIT_RUPEES = {
    "rupees": Decimal(1),
    "thousand": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lac": Decimal(100_000),
    "million": Decimal(1_000_000),
    "crore": Decimal(10_000_000),
}
_UNIT = re.compile(
    r"(?:\bin\s+|₹\s*|\brs\.?\s*|\binr\s+)(rupees|thousands?|lakhs?|lacs?|millions?|crores?)\b",
    re.I,
)
_MONTH_NAMES = (
    "january february march april may june july august september october november december"
)
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES.split(), start=1)}
_DATE = re.compile(r"^(\d{1,2}) ([A-Za-z]+),? (\d{4})$")  # 31 March 2024
_FY_RANGE = re.compile(r"^(\d{4})-(\d{2}|\d{4})$")  # 2023-24 or 2023-2024
_NUMBER = re.compile(r"^\(?-?[\d,]*\d(\.\d+)?\)?$|^[-–—]$")
_NOTE_REF = re.compile(r"^\d{1,3}(\.\d{1,2})?[a-z]?$")
LINE_TOLERANCE = 3.0  # points: words whose tops differ by less are on the same line
COLUMN_TOLERANCE = 12.0  # points: a value's right edge must sit this close to its period column


@dataclass(frozen=True)
class PdfValue:
    label: str
    fiscal_year: str  # "FY24" = year ended 31 March 2024
    raw: str  # as printed, e.g. "(56.67)"
    value: Decimal  # in document units, e.g. Decimal("-56.67")
    rupees: Decimal  # value times the page's unit
    unit: str  # "lakh", "crore", ...
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom; points from top-left
    statement: str  # balance_sheet | profit_and_loss | cash_flow | notes
    section: str  # nearest heading line above the row, "" if none
    note: str | None  # note reference column, if the table has one


@dataclass(frozen=True)
class PdfExtraction:
    path: Path
    scope: str  # standalone | consolidated
    fiscal_years: list[str]  # from the first statement's header, current year first
    unit: str  # unit of the first statement page
    values: list[PdfValue]


@dataclass
class Column:
    fiscal_year: str
    x0: float  # left edge of the period label
    x1: float  # right edge of the period label


def extract_pdf(path: Path) -> PdfExtraction:
    """Extract every statement value from a text PDF. Raises E101/E102/E104/E201/E202/E203."""
    with open_pdf(path) as pdf:
        pages = [(page.page_number, page_lines(page)) for page in pdf.pages]

    values: list[PdfValue] = []
    fiscal_years: list[str] = []
    units: list[str] = []
    titles: list[str] = []
    for number, lines in pages:
        statement = statement_of(lines)
        if statement is None:
            continue
        titles.extend(line_text(line) for line in lines[:TITLE_LINES])
        unit = unit_of(lines)
        if unit is None:
            raise UnitNotDetected(f"{path.name} page {number}: no unit line such as '₹ in Lakhs'")
        units.append(unit)
        page_values, columns = _page_values(lines, number, statement, unit)
        if not columns:
            raise PeriodsNotDetected(f"{path.name} page {number}: no period column headers found")
        if not fiscal_years:
            fiscal_years = [c.fiscal_year for c in columns]
        values.extend(page_values)

    if not values:
        raise NoTablesFound(f"{path.name}: no statement tables with values found")
    scope = (
        "consolidated" if any(re.search("consolidated", t, re.I) for t in titles) else "standalone"
    )
    return PdfExtraction(path, scope, fiscal_years, units[0], values)


def open_pdf(path: Path) -> Any:
    """Open a PDF with pdfplumber. Raises E101 (unreadable), E102 (encrypted), E104 (scanned)."""
    try:
        pdf = pdfplumber.open(path)
    except PdfminerException as exc:
        if isinstance(exc.args[0] if exc.args else None, PDFEncryptionError):
            raise FileEncrypted(f"{path.name}: the PDF is password protected") from exc
        raise FileUnreadable(f"{path.name}: not a readable PDF ({exc})") from exc
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc
    if not any(page.chars for page in pdf.pages):
        pdf.close()
        raise ScannedPdf(f"{path.name}: no text layer (scanned PDFs are out of scope)")
    return pdf


def page_lines(page: Any) -> list[list[dict[str, Any]]]:
    """Group a page's words into lines, top to bottom, each line left to right. Raises E101."""
    try:
        words = page.extract_words(extra_attrs=["fontname"])
    except PSException as exc:
        raise FileUnreadable(f"page {page.page_number} cannot be parsed ({exc})") from exc
    words.sort(key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict[str, Any]]] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][0]["top"]) < LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def line_text(line: list[dict[str, Any]]) -> str:
    return " ".join(w["text"] for w in line)


def statement_of(lines: list[list[dict[str, Any]]]) -> str | None:
    """The statement a page holds, from its title lines, or None (cover, auditor's report)."""
    for line in lines[:TITLE_LINES]:
        text = line_text(line)
        for statement, title in STATEMENT_TITLES:
            if title.search(text):
                return statement
    return None


def unit_of(lines: list[list[dict[str, Any]]]) -> str | None:
    """The unit named on the page ("lakh", "crore", ...), or None."""
    for line in lines:
        match = _UNIT.search(line_text(line))
        if match:
            word = match.group(1).lower().rstrip("s")
            return "rupees" if word == "rupee" else word
    return None


def header_columns(line: list[dict[str, Any]]) -> list[Column]:
    """Period columns in a header line ('31 March 2024' or '2023-24'). Empty if fewer than 2."""
    columns: list[Column] = []
    i = 0
    while i < len(line):
        three = " ".join(w["text"] for w in line[i : i + 3])
        date = _DATE.match(three) if i + 3 <= len(line) else None
        if date and date.group(2).lower() in _MONTHS:
            month, year = _MONTHS[date.group(2).lower()], int(date.group(3))
            fy = year if month <= 3 else year + 1  # Indian fiscal year ends 31 March
            columns.append(Column(f"FY{fy % 100:02d}", line[i]["x0"], line[i + 2]["x1"]))
            i += 3
            continue
        fy_range = _FY_RANGE.match(line[i]["text"])
        if fy_range:
            fy = int(fy_range.group(2)) % 100
            columns.append(Column(f"FY{fy:02d}", line[i]["x0"], line[i]["x1"]))
        i += 1
    return columns if len(columns) >= 2 else []


def parse_number(raw: str) -> Decimal:
    """'1,234.56' -> 1234.56; '(56.67)' -> -56.67; a lone dash means nil -> 0."""
    if raw in ("-", "–", "—"):
        return Decimal(0)
    negative = raw.startswith("(") and raw.endswith(")")
    value = Decimal(raw.strip("()").replace(",", ""))
    return -value if negative else value


def split_row(
    line: list[dict[str, Any]], columns: list[Column]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Split a line into its leading words and its values {fiscal_year: word}.

    Values are the trailing number words whose right edge sits under a period column.
    """
    values: dict[str, dict[str, Any]] = {}
    cut = len(line)
    for i in range(len(line) - 1, -1, -1):
        word = line[i]
        if not _NUMBER.match(word["text"]):
            break
        column = min(columns, key=lambda c: abs(c.x1 - word["x1"]))
        if abs(column.x1 - word["x1"]) > COLUMN_TOLERANCE or column.fiscal_year in values:
            break
        values[column.fiscal_year] = word
        cut = i
    ordered = {c.fiscal_year: values[c.fiscal_year] for c in columns if c.fiscal_year in values}
    return line[:cut], ordered


def _page_values(
    lines: list[list[dict[str, Any]]], page: int, statement: str, unit: str
) -> tuple[list[PdfValue], list[Column]]:
    """Values of one statement page, and the first header's columns ([] if no header)."""
    found: list[PdfValue] = []
    first_columns: list[Column] = []
    columns: list[Column] = []
    note_x: float | None = None
    section = ""
    for line in lines:
        header = header_columns(line)
        if header:
            columns = header
            first_columns = first_columns or header
            continue
        if not columns:
            # A "Note" column heading can sit on a line above or beside the periods.
            note_x = next((w["x0"] for w in line if w["text"].lower() == "note"), note_x)
            section = line_text(line)
            continue
        words, row = split_row(line, columns)
        if not row:
            section = line_text(line)
            continue
        note = None
        if note_x is not None and words and _NOTE_REF.match(words[-1]["text"]):
            if words[-1]["x0"] >= note_x - COLUMN_TOLERANCE:
                note = words.pop()["text"]
        label = line_text(words)
        for fiscal_year, word in row.items():
            value = parse_number(word["text"])
            found.append(
                PdfValue(
                    label=label,
                    fiscal_year=fiscal_year,
                    raw=word["text"],
                    value=value,
                    rupees=value * _UNIT_RUPEES[unit],
                    unit=unit,
                    page=page,
                    bbox=(word["x0"], word["top"], word["x1"], word["bottom"]),
                    statement=statement,
                    section=section,
                    note=note,
                )
            )
    return found, first_columns


def render_crop(
    path: Path,
    page: int,
    bbox: tuple[float, float, float, float],
    out: Path,
    margin: float = 24.0,
    resolution: int = 150,
) -> Path:
    """Write a PNG of the full-width band around `bbox` on `page` (1-based), bbox highlighted.

    The band spans the page width so the row label shows next to the value.
    """
    with open_pdf(path) as pdf:
        p = pdf.pages[page - 1]
        _, top, _, bottom = bbox
        region = (
            0.0,
            max(0.0, top - margin),
            float(p.width),
            min(float(p.height), bottom + margin),
        )
        image = p.crop(region).to_image(resolution=resolution)
        image.draw_rect(bbox, stroke=(220, 0, 0), fill=(255, 230, 0, 60), stroke_width=2)
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
    return out
