"""Extract statement values from a text PDF annual report, with page and bounding box.

Tables are read from word positions, not ruled lines, so the same code handles tables drawn with
whitespace only and tables with rules. A landscape page printed as two portrait pages side by side
is read as two halves. On each statement page (balance sheet, profit and loss, cash flow, notes):

1. Words are grouped into lines by their vertical position.
2. The page title says which statement it is, and whether standalone or consolidated (scope).
3. A column header is a block of one to five lines whose words, grouped into horizontal stacks,
   name two or more period ends: "31 March 2024", "March 31, 2025", "31st March, 2025",
   "31.03.2025", "2023-24". A stack is one column; a period split over lines ("As at March 31," /
   "2024") is read as one. Only annual columns (ending in March) are read; a page with quarter or
   half-year columns is skipped with a notice.
4. A line whose trailing numbers sit under every period column is a value row. The words to their
   left are the label, minus a note reference when the header has a "Note" column. A row with no
   label takes its label from a line just above it (a label wrapped around its figures, whose tail
   just below is added too), or else from the heading it sits under (an unlabelled subtotal).
5. Other lines are headings. A heading numbered "(a)" / "i)" is a sub-heading; the section of a row
   is the nearest heading above it that is not a sub-heading.

A statement page without its own header or unit continues the page before it when that page holds
the same statement and scope. A page that still cannot be read is skipped and reported in
`PdfExtraction.skipped`; the document fails only when no statement page can be read.

Every value keeps its raw text, the parsed `Decimal` in document units, the same in rupees, the
1-based page number and the word's bbox `(x0, top, x1, bottom)` in PDF points from the page's
top-left corner (pdfplumber's convention, so `page.crop(bbox)` shows it).
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.psexceptions import PSException
from pdfplumber.utils.exceptions import PdfminerException

from fin.audit.errors import (
    DocumentFailure,
    FileEncrypted,
    FileUnreadable,
    NoTablesFound,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)
from fin.extract.units import RUPEES_PER_UNIT, unit_in
from fin.map.normalize import normalize_label

Word = dict[str, Any]
Line = list[Word]

# A statement title starts its line: "Standalone Balance Sheet as at ...", "Statement of Profit and
# Loss for the year ...", "Consolidated Statement of Cash Flows", "Cash Flow Statement". Text that
# merely mentions one ("... forming part of the balance sheet", "Balance Sheet :") is not a title.
_SCOPE = r"(?:(standalone|consolidated)\s+)?"
_TITLE_TAIL = r"\s*(?:$|\(|(?:as\s+(?:at|on)|for|of)\b)"
STATEMENT_TITLES = (
    ("balance_sheet", re.compile(rf"^{_SCOPE}balance\s+sheet{_TITLE_TAIL}", re.I)),
    (
        "profit_and_loss",
        re.compile(
            rf"^{_SCOPE}(?:statement\s+of\s+{_SCOPE}profit\s+(?:and|&)\s+loss"
            rf"|profit\s+(?:and|&)\s+loss\s+(?:account|statement)){_TITLE_TAIL}",
            re.I,
        ),
    ),
    (
        "cash_flow",
        re.compile(
            rf"^{_SCOPE}(?:statement\s+of\s+{_SCOPE}cash\s+flows?|cash\s+flows?\s+statement)"
            rf"{_TITLE_TAIL}",
            re.I,
        ),
    ),
    ("notes", re.compile(r"^notes\b", re.I)),
)
PRIMARY_STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")
# Quarterly or half-year results ("Statement of Unaudited Standalone Financial Results for the
# quarter ended ..."): not read yet, reported as such.
_INTERIM_TITLE = re.compile(
    r"financial\s+results\b.*\b(quarter|half[\s-]+year|nine\s+months)", re.I
)
TITLE_LINES = 6  # how many lines from the top of a page can hold its title
ANNUAL_PERIOD_END_MONTH = 3  # Indian fiscal years end on 31 March

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_ORDINAL = r"(?:st|nd|rd|th)?"
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})\b")  # 31.03.2025
_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}}){_ORDINAL}[\s,.-]*([a-z]{{3,9}})\.?[\s,.-]*(\d{{4}})\b", re.I
)
_MONTH_FIRST = re.compile(rf"\b([a-z]{{3,9}})\.?\s*(\d{{1,2}}){_ORDINAL}\s*,?\s*(\d{{4}})\b", re.I)
_FY_RANGE = re.compile(r"\b(\d{4})\s*[-–]\s*(\d{2}|\d{4})\b")  # 2023-24 or 2023-2024
_DASHES = ("-", "–", "—")
_NUMBER = re.compile(r"^\(?-?(?:[\d,]*\d(?:\.\d+)?|\.\d+)\)?$|^[-–—]$")
_YEAR = re.compile(r"^(19|20)\d\d$")
_NOTE_REF = re.compile(r"^\d{1,3}(\.\d{1,2})?[a-z]?(\([a-z0-9]{1,4}\))*$|^&$|^and$", re.I)
_SUB_HEADING = re.compile(r"^\(?\s*[a-z]{1,4}\s*\)", re.I)  # "(a)", "b)", "(iii)", "(c )"
_WORDY = re.compile(r"[a-z]{3}", re.I)
_NOTES_BLOCK = re.compile(r"^notes?\s*:?$", re.I)  # "Notes:" ends the table on a statement page

LINE_TOLERANCE = 3.0  # points: words whose tops differ by less are on the same line
COLUMN_TOLERANCE = 12.0  # points: a value must overlap its column widened by this much
WORD_GAP = 6.0  # points: words closer than this on a line belong to the same header stack
HEADER_GAP = 12.0  # points: the most space between two lines of one column header
HEADER_LINES = 5  # the most lines one column header can span
WRAP_FACTOR = 0.8  # a line nearer than this many text heights belongs to the row it touches
SPREAD_STRADDLE = 2  # a landscape page with at most this many words across its middle is a spread


@dataclass(frozen=True)
class PdfValue:
    label: str
    fiscal_year: str  # "FY24" = year ended 31 March 2024
    period_end: date
    raw: str  # as printed, e.g. "(56.67)"
    value: Decimal  # in document units, e.g. Decimal("-56.67")
    rupees: Decimal  # value times the page's unit
    unit: str  # "lakh", "crore", ...
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom; points from top-left
    statement: str  # balance_sheet | profit_and_loss | cash_flow | notes
    scope: str | None  # standalone | consolidated, None when the page title does not say
    section: str  # nearest heading line above the row, "" if none
    note: str | None  # note reference column, if the table has one


@dataclass(frozen=True)
class SkippedPage:
    page: int
    statement: str
    error: DocumentFailure  # why: E202 (no annual period columns) or E203 (no unit)


@dataclass(frozen=True)
class PdfExtraction:
    path: Path
    fiscal_years: list[str]  # from the first statement's header, current year first
    unit: str  # unit of the first statement page
    values: list[PdfValue]
    skipped: list[SkippedPage] = field(default_factory=list)


@dataclass
class Column:
    period_end: date
    x0: float  # left edge of the column's header words
    x1: float  # right edge of the column's header words

    @property
    def fiscal_year(self) -> str:
        return f"FY{self.period_end.year % 100:02d}"

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class _Row:
    label: str
    values: dict[str, Word]
    columns: list[Column]
    section: str
    note: str | None
    top: float
    height: float
    wrapped: bool  # label taken from the line above; the line below may hold its tail


@dataclass
class _Page:
    number: int
    statement: str
    scope: str | None
    unit: str
    columns: list[Column]


class NotAnnual(PeriodsNotDetected):
    """A header whose period columns are not all fiscal-year ends (quarterly, half-year)."""


def extract_pdf(path: Path) -> PdfExtraction:
    """Extract every readable statement value from a text PDF.

    Raises E101/E102/E104, and E201/E202/E203 when no statement page can be read at all.
    """
    with open_pdf(path) as pdf:
        parts = [(page.page_number, lines) for page in pdf.pages for lines in page_parts(page)]

    values: list[PdfValue] = []
    skipped: list[SkippedPage] = []
    fiscal_years: list[str] = []
    units: list[str] = []
    previous: _Page | None = None
    for number, lines in parts:
        statement = statement_of(lines)
        if statement is None:
            interim = _interim_title(lines)
            if interim:
                skipped.append(
                    SkippedPage(
                        number,
                        "results",
                        PeriodsNotDetected(
                            f"page {number}: {interim!r} is a quarterly or half-year results "
                            "statement; only annual reports are supported for now"
                        ),
                    )
                )
            previous = None
            continue
        scope = scope_of(lines)
        continues = (
            previous
            if previous is not None
            and (previous.statement, previous.scope) == (statement, scope)
            and number - previous.number <= 1
            else None
        )
        unit = unit_of(lines) or (continues.unit if continues else None)
        try:
            if unit is None:
                raise UnitNotDetected(f"page {number}: no unit line such as '₹ in Lakhs'")
            page_values, columns = read_page(
                lines, number, statement, scope, unit, continues.columns if continues else []
            )
            if not columns:
                raise PeriodsNotDetected(
                    f"page {number}: no period column headers (such as 'As at 31 March 2025')"
                )
        except (UnitNotDetected, PeriodsNotDetected) as exc:
            if statement != "notes":
                skipped.append(SkippedPage(number, statement, exc))
            previous = None
            continue
        previous = _Page(number, statement, scope, unit, columns)
        if statement != "notes":
            units.append(unit)
            fiscal_years = fiscal_years or [c.fiscal_year for c in columns]
        values.extend(page_values)

    values = _without_repeats(values)
    if not any(v.statement in PRIMARY_STATEMENTS for v in values):
        if skipped:
            reasons = "; ".join(s.error.message for s in skipped[:5])
            more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
            raise type(skipped[0].error)(
                f"{path.name}: no statement page could be read: {reasons}{more}"
            )
        raise NoTablesFound(f"{path.name}: no statement tables with values found")
    return PdfExtraction(path, fiscal_years, units[0], values, skipped)


def _without_repeats(values: list[PdfValue]) -> list[PdfValue]:
    """Drop a value printed twice in one statement (same scope, section, label, year and figure),
    such as a net change in cash repeated under the closing balance."""
    seen: set[tuple[object, ...]] = set()
    kept = []
    for v in values:
        key = (v.statement, v.scope, v.section, normalize_label(v.label), v.fiscal_year, v.value)
        if v.statement != "notes" and key in seen:
            continue
        seen.add(key)
        kept.append(v)
    return kept


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


def page_parts(page: Any) -> list[list[Line]]:
    """A page's lines, or the lines of each half of a landscape page printed as two portrait pages
    side by side (no words cross its middle). Raises E101."""
    try:
        words = page.extract_words(extra_attrs=["fontname"])
    except PSException as exc:
        raise FileUnreadable(f"page {page.page_number} cannot be parsed ({exc})") from exc
    middle = float(page.width) / 2
    straddling = sum(1 for w in words if w["x0"] < middle < w["x1"])
    if page.width > page.height and words and straddling <= SPREAD_STRADDLE:
        left = [w for w in words if (w["x0"] + w["x1"]) / 2 < middle]
        right = [w for w in words if (w["x0"] + w["x1"]) / 2 >= middle]
        return [_lines(half) for half in (left, right) if half]
    return [_lines(words)]


def page_lines(page: Any) -> list[Line]:
    """Group a page's words into lines, top to bottom, each line left to right. Raises E101."""
    return [line for part in page_parts(page) for line in part]


def _lines(words: list[Word]) -> list[Line]:
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[Line] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][0]["top"]) < LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def line_text(line: Line) -> str:
    return " ".join(w["text"] for w in line)


def _title(lines: list[Line]) -> tuple[str, re.Match[str]] | None:
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        for statement, title in STATEMENT_TITLES:
            match = title.search(text)
            if match:
                return statement, match
    return None


def statement_of(lines: list[Line]) -> str | None:
    """The statement a page holds, from its title lines, or None (cover, auditor's report)."""
    found = _title(lines)
    return found[0] if found else None


def scope_of(lines: list[Line]) -> str | None:
    """'standalone' or 'consolidated' from the title, else from the other top lines (a running
    header such as 'Consolidated Financial Statements'); None when they do not say."""
    found = _title(lines)
    if found:
        words = [g for g in found[1].groups() if g]
        if words:
            return words[0].lower()
    for line in lines[:TITLE_LINES]:
        match = re.search(r"\b(standalone|consolidated)\b", line_text(line), re.I)
        if match:
            return match.group(1).lower()
    return None


def _interim_title(lines: list[Line]) -> str | None:
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        if _INTERIM_TITLE.search(text):
            return text
    return None


def unit_of(lines: list[Line]) -> str | None:
    """The unit named on the page ("lakh", "crore", ...), or None."""
    for line in lines:
        unit = unit_in(line_text(line))
        if unit is not None:
            return unit
    return None


def period_end_in(text: str) -> date | None:
    """The period end a column heading names: '31 March 2024', 'March 31, 2025', '31st March,
    2025', '31.03.2025', '31-Mar-2025' or '2023-24' (year ended 31 March 2024). None if it names no
    date, or a date that is not a month end (such as '1 April 2023', an opening balance)."""
    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        return _month_end(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    for pattern, day_group, month_group in ((_DAY_FIRST, 1, 2), (_MONTH_FIRST, 2, 1)):
        for match in pattern.finditer(text):
            month = _MONTHS.get(match.group(month_group).lower())
            if month is not None:
                return _month_end(int(match.group(3)), month, int(match.group(day_group)))
    fy_range = _FY_RANGE.search(text)
    if fy_range:
        start, end = int(fy_range.group(1)), int(fy_range.group(2))
        if end % 100 == (start + 1) % 100:
            return date(start + 1, ANNUAL_PERIOD_END_MONTH, 31)
    return None


def _month_end(year: int, month: int, day: int) -> date | None:
    if not 1 <= month <= 12 or day != calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def _is_value_like(line: Line) -> bool:
    """True when a line ends in a figure (not a bare year), so it cannot be part of a header."""
    last = line[-1]["text"]
    return bool(_NUMBER.match(last)) and not _YEAR.match(last)


def _stacks(block: list[Line]) -> list[list[Word]]:
    """Group a header block's words into column stacks: runs of close words on a line, merged with
    runs on the other lines that overlap them horizontally."""
    runs: list[list[Word]] = []
    for line in block:
        run: list[Word] = []
        for word in line:
            if run and word["x0"] - run[-1]["x1"] > WORD_GAP:
                runs.append(run)
                run = []
            run.append(word)
        runs.append(run)
    stacks: list[tuple[float, float, list[Word]]] = []
    for run in runs:
        x0, x1, words = run[0]["x0"], run[-1]["x1"], list(run)
        keep = []
        for sx0, sx1, swords in stacks:
            if sx0 <= x1 and x0 <= sx1:
                x0, x1, words = min(x0, sx0), max(x1, sx1), swords + words
            else:
                keep.append((sx0, sx1, swords))
        stacks = [*keep, (x0, x1, words)]
    return [sorted(words, key=lambda w: (w["top"], w["x0"])) for _, _, words in stacks]


def block_columns(block: list[Line]) -> list[Column]:
    """The period columns a header block names, left to right; [] if fewer than two. When a period
    appears twice (a title date and a column), the rightmost is the column."""
    found: dict[date, Column] = {}
    for stack in _stacks(block):
        period = period_end_in(" ".join(w["text"] for w in stack))
        if period is None:
            continue
        column = Column(period, min(w["x0"] for w in stack), max(w["x1"] for w in stack))
        if period not in found or column.x0 > found[period].x0:
            found[period] = column
    columns = sorted(found.values(), key=lambda c: c.x0)
    return columns if len(columns) >= 2 else []


def header_at(lines: list[Line], i: int) -> tuple[list[Column], int] | None:
    """The shortest column header starting at line i, and the index of the line after it."""
    for end in range(i, min(i + HEADER_LINES, len(lines))):
        line = lines[end]
        if _is_value_like(line):
            return None
        if end > i and line[0]["top"] - lines[end - 1][0]["top"] > HEADER_GAP:
            return None
        columns = block_columns(lines[i : end + 1])
        if columns:
            return columns, end + 1
    return None


def header_columns(line: Line) -> list[Column]:
    """Period columns named in a single line; [] if fewer than two."""
    return block_columns([line])


def parse_number(raw: str) -> Decimal:
    """'1,234.56' -> 1234.56; '(56.67)' -> -56.67; '.82' -> 0.82; a lone dash means nil -> 0."""
    if raw in _DASHES:
        return Decimal(0)
    negative = raw.startswith("(") and raw.endswith(")") or raw.startswith("-")
    value = Decimal(raw.strip("()").lstrip("-").replace(",", ""))
    return -value if negative else value


def split_row(line: Line, columns: list[Column]) -> tuple[Line, dict[str, Word]]:
    """Split a line into its leading words and its values {fiscal_year: word}.

    Values are the trailing number words, each under a different period column (nearest column
    centre, overlapping the column widened by COLUMN_TOLERANCE). A line is a value row only when
    every column has a value; otherwise the values are {}.
    """
    values: dict[str, Word] = {}
    cut = len(line)
    for i in range(len(line) - 1, -1, -1):
        word = line[i]
        if not _NUMBER.match(word["text"]):
            break
        column = min(columns, key=lambda c: abs(c.center - (word["x0"] + word["x1"]) / 2))
        overlaps = (
            word["x0"] <= column.x1 + COLUMN_TOLERANCE
            and word["x1"] >= column.x0 - COLUMN_TOLERANCE
        )
        if not overlaps or column.fiscal_year in values:
            break
        values[column.fiscal_year] = word
        cut = i
    if len(values) < len(columns):
        return line, {}
    return line[:cut], {c.fiscal_year: values[c.fiscal_year] for c in columns}


def read_page(
    lines: list[Line],
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
    columns: list[Column],
) -> tuple[list[PdfValue], list[Column]]:
    """Values of one statement page, and the columns in force at its end ([] if none).

    `columns` are the columns carried over from the page before (a continued statement).
    Raises NotAnnual (E202) when a statement page's header has quarter or half-year columns.
    """
    rows: list[_Row] = []
    note_x: float | None = None
    section = ""
    before_heading = ""  # the section before the last heading line, to undo a wrapped label
    last_text: Line | None = None  # the line just read, when it was a text line
    i = 0
    while i < len(lines):
        found = header_at(lines, i)
        later = header_at(lines, i + 1) if found else None
        if found and (later is None or later[1] != found[1]):
            header, i = found
            if any(c.period_end.month != ANNUAL_PERIOD_END_MONTH for c in header):
                ends = ", ".join(c.period_end.isoformat() for c in header)
                if statement != "notes":
                    raise NotAnnual(
                        f"page {page}: period columns end {ends}; quarterly and half-year "
                        "columns are not supported yet"
                    )
                columns = []
                continue
            columns = header
            last_text = None
            continue
        line = lines[i]
        i += 1
        text = line_text(line)
        words, row = split_row(line, columns) if columns else (line, {})
        if not row:
            if not columns and any(w["text"].lower() in ("note", "notes") for w in line):
                note_x = next(w["x0"] for w in line if w["text"].lower() in ("note", "notes"))
            if columns and statement != "notes" and _NOTES_BLOCK.match(text):
                break
            if columns and line[0]["x0"] >= columns[0].x0 - COLUMN_TOLERANCE:
                continue  # column sub-heading ("(Restated)") or margin text, not a heading
            previous = rows[-1] if rows else None
            if (
                previous is not None
                and previous.wrapped
                and last_text is None
                and line[0]["top"] - previous.top < WRAP_FACTOR * previous.height
            ):
                previous.label = f"{previous.label} {text}"
                continue
            last_text = line
            if not _WORDY.search(text):
                continue
            before_heading = section
            if not _SUB_HEADING.match(text):
                section = text
            continue
        note = None
        if note_x is not None:
            notes: list[str] = []
            while words and _NOTE_REF.match(words[-1]["text"]):
                if words[-1]["x0"] < note_x - COLUMN_TOLERANCE:
                    break
                notes.insert(0, words.pop()["text"])
            note = " ".join(notes) or None
        label = line_text(words)
        top = line[0]["top"]
        height = max(w["bottom"] - w["top"] for w in line)
        wrapped = False
        if not label:
            if last_text is not None and top - last_text[0]["top"] < WRAP_FACTOR * height:
                label, wrapped = line_text(last_text), True
                section = before_heading
            else:
                label = section
        rows.append(_Row(label, row, columns, section, note, top, height, wrapped))
        last_text = None
        if all(w["text"] in _DASHES for w in row.values()) and not _SUB_HEADING.match(label):
            section = label  # a heading printed with nil dashes ("Current assets - -")
    found_values = [
        PdfValue(
            label=r.label,
            fiscal_year=fiscal_year,
            period_end=next(c.period_end for c in r.columns if c.fiscal_year == fiscal_year),
            raw=word["text"],
            value=parse_number(word["text"]),
            rupees=parse_number(word["text"]) * RUPEES_PER_UNIT[unit],
            unit=unit,
            page=page,
            bbox=(word["x0"], word["top"], word["x1"], word["bottom"]),
            statement=statement,
            scope=scope,
            section=r.section,
            note=r.note,
        )
        for r in rows
        for fiscal_year, word in r.values.items()
    ]
    return found_values, columns


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
