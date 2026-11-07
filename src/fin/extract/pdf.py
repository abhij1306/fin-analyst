"""Extract statement values from a text PDF (annual report or quarterly results), with page and
bounding box.

Tables are read from word positions, not ruled lines, so the same code handles tables drawn with
whitespace only and tables with rules. A landscape page printed as two portrait pages side by side
is read as two halves. On each statement page (balance sheet, profit and loss, cash flow, notes):

1. Words are grouped into lines by their vertical position.
2. The page title says which statement it is, and whether standalone or consolidated (scope). A
   quarterly or half-year results statement ("Statement of Unaudited Standalone Financial Results
   for the quarter ended ...") is a profit and loss statement; "Statement of assets and
   liabilities" is a balance sheet. In a results document, a statement numbered as a note ("1.
   Balance Sheet as on", "2. Cash flow statement") is read too.
3. A column header is a block of one to five lines whose words, grouped into horizontal stacks,
   name two or more period ends: "31 March 2024", "March 31, 2025", "31st March, 2025",
   "31.03.2025", "30-Sep-25", "2023-24". A stack is one column; a period split over lines ("As at
   March 31," / "2024") is read as one. Words in or just above the header that only name a period
   length ("Quarter ended", "Half year ended", "Year ended") are spread over the columns below
   them, so one page can hold quarter, half-year and year columns. `fin.extract.periods` names
   each column's period ("FY25", "FY26-Q1", "FY26-H1"). A flow column that names no length is a
   year only when it ends on 31 March and the page is not a results statement; otherwise the page
   is skipped (E202), as it is when two columns hold the same period (such as standalone and
   consolidated printed side by side).
4. A line whose trailing numbers sit under every period column is a value row. The words to their
   left are the label, minus a note reference when the header has a "Note" column. A row with no
   label takes its label from a line just above it (a label wrapped around its figures, whose tail
   just below is added too), or else from the heading it sits under (an unlabelled subtotal).
5. Other lines are headings. A heading numbered "(a)" / "i)" is a sub-heading; the section of a row
   is the nearest heading above it that is not a sub-heading.

A statement page without its own header or unit continues the page before it when that page holds
the same statement and scope. A page that still cannot be read is skipped and reported in
`PdfExtraction.skipped`; the document fails only when no statement page can be read. So is a page
whose figures look misread: a row label that holds a figure (the figures do not line up with their
labels) or a figure with an unpaired bracket (its sign is unknown). fin reads clean digital
statements; a page it skips is listed for a Claude reading, never loaded wrong.

Every value keeps its raw text, the parsed `Decimal` in document units, the same in rupees, the
1-based page number and the word's bbox `(x0, top, x1, bottom)` in PDF points from the page's
top-left corner (pdfplumber's convention, so `page.crop(bbox)` shows it).
"""

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from itertools import combinations, pairwise
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
    ParseError,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)
from fin.extract.periods import PHRASE, fiscal_year_of, months_in, period_of, resolve_months
from fin.extract.units import RUPEES_PER_UNIT, unit_in

Word = dict[str, Any]
Line = list[Word]

# A statement title starts its line: "Standalone Balance Sheet as at ...", "Statement of Profit and
# Loss for the year ...", "Consolidated Statement of Cash Flows", "Cash Flow Statement". Text that
# merely mentions one ("... forming part of the balance sheet", "Balance Sheet :") is not a title.
_SCOPE = r"(?:(standalone|consolidated)\s+)?"
_TITLE_TAIL = r"\s*(?:$|\(|(?:as\s+(?:at|on)|for|of)\b)"
_AUDITED = r"(?:(?:un-?\s*audited|audited|reviewed)\s+)?"
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
    # Results under SEBI Reg 33: "Statement of Unaudited Standalone Financial Results for the
    # quarter ended ...", "Standalone Statement of Un-Audited Financial Results ...".
    (
        "profit_and_loss",
        re.compile(
            rf"^{_SCOPE}statement\s+of\s+{_AUDITED}{_SCOPE}{_AUDITED}financial\s+results\b",
            re.I,
        ),
    ),
    (
        "balance_sheet",
        re.compile(rf"^{_SCOPE}statement\s+of\s+{_SCOPE}assets\s+(?:and|&)\s+liabilities", re.I),
    ),
    ("notes", re.compile(r"^notes\b", re.I)),
)
PRIMARY_STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")
_RESULTS_TITLE = re.compile(r"financial\s+results\b", re.I)
# In a results document, a statement numbered as a note ("1. Balance Sheet as on").
_NUMBERED = re.compile(r"^\d{1,2}\s*[.)]\s*")
# Top-of-page text that starts something other than a statement continued from the page before.
_OTHER_TITLE = re.compile(r"\bstatement\b|\breport\b|^notes\b|\bnotes\s+(?:to|forming)\b", re.I)
TITLE_LINES = 6  # how many lines from the top of a page can hold its title
UNREAD_FIGURE_LINES = 5  # figure lines that make an untitled statement page reported
# Words that name a primary statement in a title, however it is worded.
_STATEMENT_WORDS = re.compile(
    r"\b(?:balance\s+sheet|statement\s+of\s+assets\s+(?:and|&)\s+liab\w*|profit\s+(?:and|&)\s+loss"
    r"|statement\s+of\s+cash\s*flows?|cash\s*flows?\s+statement|financial\s+results"
    r"|cash\s*flows?\s+from\s+operating\s+activities)\b",
    re.I,
)
TITLE_WORDS = 5  # the statement words of a title start within its first five words
NAMED_LINES = 12  # top lines searched for them, down to the first figure (below a letterhead)
_FIGURE_TEXT = re.compile(r"\d[,.]\d")  # a figure with a comma or decimal point, not a page number
LABEL_LINES = 4  # how many lines above a column header can hold period-length labels

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
_DAY_FIRST = re.compile(  # 31 March 2025, 31-Mar-2025, 30-Sep-25
    rf"\b(\d{{1,2}}){_ORDINAL}[\s,.-]*([a-z]{{3,9}})\.?[\s,.-]*(\d{{4}}|\d{{2}})\b", re.I
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
INDENT = 2.0  # points: a line starting this much further right is indented
TOUCHING = 0.5  # points: figure pieces closer than this are one figure
SPLIT_GAP = 2.5  # points: pieces this close are one figure when only together they make one
_FIGURE_PART = re.compile(r"^\(?[\d,.]+\)?$|^\($")
_FIGURE = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{2,3})*|\d+)(?:\.\d+)?\)?$")
# A label ending in one of these words carries on to the next line.
_DANGLING = re.compile(r"\b(and|or|of|to|than|in|for|the|from|with|on|by)$", re.I)


@dataclass(frozen=True)
class PdfValue:
    label: str
    fiscal_year: str  # "FY24" = the fiscal year ended 31 March 2024
    period: str  # "FY24", "FY25-Q1", "FY25-H1", "FY25-9M" (fin.extract.periods)
    months: int  # length of the period: 3, 6, 9 or 12
    period_end: date
    raw: str  # as printed, e.g. "(56.67)"
    value: Decimal  # in document units, e.g. Decimal("-56.67")
    rupees: Decimal  # value times the page's unit
    unit: str  # "lakh", "crore", ...
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom; points from top-left
    statement: str  # balance_sheet | profit_and_loss | cash_flow | notes
    scope: str | None  # standalone | consolidated, None when the title does not say
    section: str  # nearest heading line above the row, "" if none
    note: str | None  # note reference column, if the table has one


@dataclass(frozen=True)
class SkippedPage:
    page: int
    statement: str
    error: DocumentFailure  # E201 (no full row, unrecognised title), E202, E203, E204 (misread)

    def as_dict(self) -> dict[str, object]:
        return {
            "page": self.page,
            "statement": self.statement,
            "code": self.error.code,
            "reason": self.error.message,
        }


@dataclass(frozen=True)
class PdfExtraction:
    path: Path
    fiscal_years: list[str]  # periods of the first statement's header, latest first
    unit: str  # unit of the first statement page
    values: list[PdfValue]
    skipped: list[SkippedPage] = field(default_factory=list)


@dataclass
class Column:
    period_end: date
    x0: float  # left edge of the column's header words
    x1: float  # right edge of the column's header words
    months: int | None = None  # period length its header names (fin.extract.periods.months_in)
    period: str | None = None  # "FY25", "FY26-Q1", ...: set once the statement is known

    @property
    def fiscal_year(self) -> str:
        return fiscal_year_of(self.period_end)

    @property
    def key(self) -> str:
        """Tells apart the columns of one header (two can end on the same date)."""
        return f"{self.period_end.isoformat()}|{self.months}"

    @property
    def label(self) -> str:
        """The column's name in a Markdown table: 'FY25', or its length and date."""
        return self.period or (
            self.fiscal_year
            if self.months in (None, 12)
            else f"{self.months or 'YTD'}M to {self.period_end}"
        )

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
    failed: DocumentFailure | None = None  # why the page was not read


def extract_pdf(path: Path) -> PdfExtraction:
    """Extract every readable statement value from a text PDF.

    Raises E101/E102/E104, and E201/E202/E203 when no statement page can be read at all.
    """
    with open_pdf(path) as pdf:
        parts = [(page.page_number, lines) for page in pdf.pages for lines in page_parts(page)]

    results = any(_is_results(lines) for _, lines in parts)
    values: list[PdfValue] = []
    skipped: list[SkippedPage] = []
    fiscal_years: list[str] = []
    units: list[str] = []
    previous: _Page | None = None
    for number, lines in parts:
        statement = statement_of(lines, numbered=results)
        scope = scope_of(lines, numbered=results)
        if statement is None and previous is not None and _continues(previous, number, lines):
            statement, scope = previous.statement, previous.scope
            if previous.failed is not None:  # carries on a page that was not read: nor is it
                if _figure_lines(lines) >= UNREAD_FIGURE_LINES:
                    reason = f"page {number}: carries on page {previous.number}, which was not read"
                    skipped.append(SkippedPage(number, statement, type(previous.failed)(reason)))
                previous = None
                continue
        if statement is None:
            untitled = _untitled_statement(lines, number)
            if untitled is not None:
                skipped.append(SkippedPage(number, *untitled))
            previous = None
            continue
        continues = (
            previous
            if previous is not None
            and previous.failed is None
            and (previous.statement, previous.scope) == (statement, scope)
            and number - previous.number <= 1
            else None
        )
        unit = unit_of(lines) or (continues.unit if continues else None)
        try:
            if unit is None:
                raise UnitNotDetected(f"page {number}: no unit line such as '₹ in Lakhs'")
            page_values, columns = read_page(
                lines,
                number,
                statement,
                scope,
                unit,
                continues.columns if continues else [],
                results=_is_results(lines) or bool(continues and results),
            )
            if not columns:
                raise PeriodsNotDetected(
                    f"page {number}: no period column headers (such as 'As at 31 March 2025')"
                )
            if not page_values and statement in PRIMARY_STATEMENTS and not continues:
                raise NoTablesFound(
                    f"page {number}: titled as a statement, but no line has a figure under "
                    "every period column"
                )
        except (UnitNotDetected, PeriodsNotDetected, NoTablesFound, ParseError) as exc:
            if statement != "notes":
                skipped.append(SkippedPage(number, statement, exc))
            previous = _Page(number, statement, scope, unit or "", [], exc)
            continue
        previous = _Page(number, statement, scope, unit, columns)
        if statement != "notes":
            units.append(unit)
            fiscal_years = fiscal_years or list(dict.fromkeys(c.period or "" for c in columns))
        values.extend(page_values)

    if not any(v.statement in PRIMARY_STATEMENTS for v in values):
        if skipped:
            reasons = "; ".join(s.error.message for s in skipped[:5])
            more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
            error = type(skipped[0].error)(
                f"{path.name}: no statement page could be read: {reasons}{more}"
            )
            error.pages = tuple(s.as_dict() for s in skipped)
            raise error
        raise NoTablesFound(f"{path.name}: no statement tables with values found")
    return PdfExtraction(path, fiscal_years, units[0], values, skipped)


def _untitled_statement(lines: list[Line], number: int) -> tuple[str, DocumentFailure] | None:
    """(statement, E201) for a page whose top lines name a statement in words no title pattern
    matches, above at least UNREAD_FIGURE_LINES lines ending in a figure: a statement fin cannot
    read, reported rather than passed over in silence. None for any other page."""
    named = _named_statement(lines)
    if _figure_lines(lines) < UNREAD_FIGURE_LINES or named is None:
        return None
    statement, text = named
    return statement, NoTablesFound(
        f"page {number}: {text!r} names a statement in a title fin does not recognise; "
        "the page was not read"
    )


def _figure_lines(lines: list[Line]) -> int:
    """How many lines end in a figure written with a comma or decimal point."""
    return sum(
        1 for line in lines if _is_value_like(line) and _FIGURE_TEXT.search(line[-1]["text"])
    )


def _named_statement(lines: list[Line]) -> tuple[str, str] | None:
    """(statement, line) for the first top line (of NAMED_LINES, above the first figure) that
    reads as a title naming a primary statement within its first TITLE_WORDS words, or None. Not
    a title: a line starting in lower case or ending in a figure, '.' or ':' (a sentence, a table
    row, a lead-in)."""
    for line in lines[:NAMED_LINES]:
        text = line_text(line).strip()
        if _is_value_like(line) and _FIGURE_TEXT.search(line[-1]["text"]):
            break
        if not text or text[0].islower() or _is_value_like(line):
            continue
        if text.rstrip(" -").endswith((".", ":")):
            continue
        match = _STATEMENT_WORDS.search(text)
        if match and len(text[: match.start()].split()) < TITLE_WORDS:
            words = match.group(0).lower()
            if "sheet" in words or "assets" in words:
                return "balance_sheet", text
            return ("cash_flow" if "cash" in words else "profit_and_loss"), text
    return None


def _continues(previous: _Page, number: int, lines: list[Line]) -> bool:
    """True when an untitled page part carries on the statement just read: the next page part
    (such as the second half of a balance sheet printed over two half pages), with no other title
    (statement of changes in equity, notes, a report, or words naming another statement) at its
    top."""
    if previous.statement not in PRIMARY_STATEMENTS or number - previous.number > 1:
        return False
    if any(_OTHER_TITLE.search(line_text(line)) for line in lines[:TITLE_LINES]):
        return False
    named = _named_statement(lines)
    return named is None or named[0] == previous.statement


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
    return [_join_split_numbers(sorted(line, key=lambda w: w["x0"])) for line in lines]


def _join_split_numbers(line: Line) -> Line:
    """Rejoin a figure the PDF stores as touching pieces ('76,189.2' + '1'), or as pieces a little
    apart that only together make a figure ('9,906.' + '15', '1' + ',806.06', '(' + '0.11)')."""
    joined: Line = []
    for word in line:
        previous = joined[-1] if joined else None
        gap = word["x0"] - previous["x1"] if previous is not None else 0.0
        if (
            previous is not None
            and gap < SPLIT_GAP
            and _FIGURE_PART.match(previous["text"])
            and _FIGURE_PART.match(word["text"])
            and (
                gap < TOUCHING
                or (
                    _FIGURE.match(previous["text"] + word["text"]) is not None
                    and not (_FIGURE.match(previous["text"]) and _FIGURE.match(word["text"]))
                )
            )
        ):
            joined[-1] = {
                **previous,
                "text": previous["text"] + word["text"],
                "x1": word["x1"],
                "bottom": max(previous["bottom"], word["bottom"]),
            }
        else:
            joined.append(word)
    return joined


def line_text(line: Line) -> str:
    return " ".join(w["text"] for w in line)


def _title(lines: list[Line], numbered: bool = False) -> tuple[str, re.Match[str]] | None:
    if numbered:
        for line in lines[:TITLE_LINES]:
            text = line_text(line).strip()
            if not _NUMBERED.match(text):
                continue
            for statement, title in STATEMENT_TITLES:
                match = title.search(_NUMBERED.sub("", text))
                if match and statement in PRIMARY_STATEMENTS:
                    return statement, match
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        for statement, title in STATEMENT_TITLES:
            match = title.search(text)
            if match:
                return statement, match
    return None


def statement_of(lines: list[Line], numbered: bool = False) -> str | None:
    """The statement a page holds, from its title lines, or None (cover, auditor's report).
    `numbered`: also read a statement numbered as a note ("1. Balance Sheet as on"), as results
    documents print them."""
    found = _title(lines, numbered)
    return found[0] if found else None


def scope_of(lines: list[Line], numbered: bool = False) -> str | None:
    """'standalone' or 'consolidated' from the title; None when it does not say."""
    found = _title(lines, numbered)
    words = [g for g in found[1].groups() if g] if found else []
    return words[0].lower() if words else None


def _is_results(lines: list[Line]) -> bool:
    """True for a page titled as quarterly or half-year results."""
    found = _title(lines)
    return found is not None and _RESULTS_TITLE.search(found[1].group(0)) is not None


def unit_of(lines: list[Line]) -> str | None:
    """The unit named on the page ("lakh", "crore", ...), or None."""
    for line in lines:
        unit = unit_in(line_text(line))
        if unit is not None:
            return unit
    return None


def period_end_in(text: str) -> date | None:
    """The period end a column heading names: '31 March 2024', 'March 31, 2025', '31st March,
    2025', '31.03.2025', '31-Mar-2025', '30-Sep-25' or '2023-24' (year ended 31 March 2024). None
    if it names no date, or a date that is not a month end (such as '1 April 2023', an opening
    balance)."""
    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        return _month_end(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
    for pattern, day_group, month_group in ((_DAY_FIRST, 1, 2), (_MONTH_FIRST, 2, 1)):
        for match in pattern.finditer(text):
            month = _MONTHS.get(match.group(month_group).lower())
            if month is not None:
                year = int(match.group(3))
                year = year + 2000 if year < 100 else year
                return _month_end(year, month, int(match.group(day_group)))
    fy_range = _FY_RANGE.search(text)
    if fy_range:
        start, end = int(fy_range.group(1)), int(fy_range.group(2))
        if end % 100 == (start + 1) % 100:
            return date(start + 1, 3, 31)
    return None


def _month_end(year: int, month: int, day: int) -> date | None:
    if not 1 <= month <= 12 or day != calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def _is_value_like(line: Line) -> bool:
    """True when a line ends in a figure (not a bare year), so it cannot be part of a header."""
    last = line[-1]["text"]
    return bool(_NUMBER.match(last)) and not _YEAR.match(last)


def _runs(line: Line) -> list[list[Word]]:
    """Runs of close words on a line (gaps of at most WORD_GAP)."""
    runs: list[list[Word]] = []
    run: list[Word] = []
    for word in line:
        if run and word["x0"] - run[-1]["x1"] > WORD_GAP:
            runs.append(run)
            run = []
        run.append(word)
    if run:
        runs.append(run)
    return runs


def _stacks(block: list[Line]) -> list[list[Word]]:
    """Group a header block's words into column stacks: runs of close words on a line, merged with
    runs on the other lines that overlap them horizontally."""
    stacks: list[tuple[float, float, list[Word]]] = []
    for run in (run for line in block for run in _runs(line)):
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
    """The period columns a header block names, left to right; [] if fewer than two. Two columns
    can end on the same date (a quarter and a year ending 31 March)."""
    columns = []
    for stack in _stacks(block):
        period = period_end_in(" ".join(w["text"] for w in stack))
        if period is not None:
            columns.append(Column(period, min(w["x0"] for w in stack), max(w["x1"] for w in stack)))
    columns.sort(key=lambda c: c.x0)
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


def complete_header(lines: list[Line], start: int, end: int) -> tuple[list[Column], int]:
    """The columns of the header found at lines[start:end], and the index of the line after it.
    The period-length labels ("Quarter ended") in the header or in the few lines above it are
    spread over the columns (`spread`)."""
    columns = block_columns(lines[start:end])
    region = start
    while region > max(start - LABEL_LINES, 0) and not _is_value_like(lines[region - 1]):
        region -= 1
    runs = [run for line in lines[region:end] for run in _runs(line)]
    lengths = _labels(runs, PHRASE, columns, months_in)
    if lengths is not None:
        columns = [replace(c, months=months) for c, months in zip(columns, lengths, strict=True)]
    return columns, end


def _labels(
    runs: list[list[Word]], pattern: re.Pattern[str], columns: list[Column], value: Any
) -> list[Any] | None:
    """The value of the label over each column, from the runs that are such a label; None when
    there are none, or more labels than columns."""
    found = []
    for run in runs:
        text = line_text(run).strip()
        center = (run[0]["x0"] + run[-1]["x1"]) / 2
        if pattern.match(text) and columns[0].x0 - 3 * COLUMN_TOLERANCE <= center:
            found.append((center, value(text)))
    found.sort(key=lambda item: item[0])
    if not found or len(found) > len(columns):
        return None
    return spread(columns, found)


def spread(columns: list[Column], labels: list[tuple[float, Any]]) -> list[Any]:
    """Give each column the value of the label over it. Labels (x center, value), left to right,
    each head a run of adjacent columns; the runs chosen are those whose centers lie closest to
    their labels (least squared distance)."""
    n, k = len(columns), len(labels)
    best: tuple[float, tuple[int, ...]] | None = None
    for cuts in combinations(range(1, n), k - 1):
        bounds = (0, *cuts, n)
        cost = sum(
            (center - (columns[a].center + columns[b - 1].center) / 2) ** 2
            for (a, b), (center, _) in zip(pairwise(bounds), labels, strict=True)
        )
        if best is None or cost < best[0]:
            best = (cost, bounds)
    assert best is not None
    values: list[Any] = []
    for (a, b), (_, label) in zip(pairwise(best[1]), labels, strict=True):
        values.extend([label] * (b - a))
    return values


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
    """Split a line into its leading words and its values {column key: word}.

    Values are the trailing number words, each under a different period column. A column owns the
    band from halfway to its left neighbour to halfway to its right one; the first starts
    COLUMN_TOLERANCE left of its header and the last runs to the page edge. A line is a value row
    only when every column has a value; otherwise the values are {}.
    """
    values: dict[str, Word] = {}
    cut = len(line)
    for i in range(len(line) - 1, -1, -1):
        word = line[i]
        if not _NUMBER.match(word["text"]):
            break
        center = (word["x0"] + word["x1"]) / 2
        bounds = [(a.center + b.center) / 2 for a, b in pairwise(columns)]
        column = columns[sum(1 for bound in bounds if center > bound)]
        if column is columns[0] and word["x1"] < column.x0 - COLUMN_TOLERANCE:
            break
        if column.key in values:
            break
        values[column.key] = word
        cut = i
    if len(values) < len(columns):
        return line, {}
    return line[:cut], {c.key: values[c.key] for c in columns}


def _is_note(word: Word) -> bool:
    """A 'Note' / 'Notes' / 'Note No.' column heading word."""
    return word["text"].rstrip(".") in ("Note", "Notes", "NOTE", "NOTES")


def _is_tail(row: _Row, line: Line, text: str) -> bool:
    """True when a text line just below a value row ends that row's label: the label wrapped around
    the row's figures, or it ends in a hyphen or a word like 'and' / 'of', or the line starts in
    lower case."""
    gap = line[0]["top"] - row.top
    if row.wrapped and gap < WRAP_FACTOR * row.height:
        return True
    dangling = row.label.endswith("-") or _DANGLING.search(row.label) is not None
    return gap < 2 * row.height and (dangling or text[:1].islower())


def _join(head: str, tail: str) -> str:
    return head + tail if head.endswith("-") else f"{head} {tail}"


def resolve_columns(
    columns: list[Column], statement: str, page: int, *, results: bool
) -> list[Column]:
    """Name each column's period for this statement (`fin.extract.periods`). Raises E202 when a
    column's period cannot be told, or two columns would hold the same period."""
    where = f"page {page}"
    resolved = []
    for column in columns:
        if results and statement != "balance_sheet" and column.months is None:
            raise PeriodsNotDetected(
                f"{where}: results statement column ending {column.period_end.isoformat()} does "
                "not say whether it is a quarter, half year or year"
            )
        months = resolve_months(statement, column.period_end, column.months, where=where)
        period = period_of(column.period_end, months, where=where)
        resolved.append(replace(column, months=months, period=period))
    names = [c.period for c in resolved]
    if len(set(names)) < len(names):
        listed = ", ".join(c.period for c in resolved if c.period)
        raise PeriodsNotDetected(f"{where}: two columns hold the same period ({listed})")
    return resolved


def read_page(
    lines: list[Line],
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
    columns: list[Column],
    *,
    results: bool = False,
) -> tuple[list[PdfValue], list[Column]]:
    """Values of one statement page, and the columns in force at its end ([] if none).

    `columns` are the columns carried over from the page before (a continued statement).
    `results`: the page is a quarterly or half-year results statement, so a flow column must name
    its length. Raises PeriodsNotDetected (E202) when a header's periods cannot be told.
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
            header, end = complete_header(lines, i, found[1])
            note_x = note_x or next(
                (w["x0"] for ln in lines[i:end] for w in ln if _is_note(w)), None
            )
            i = end
            try:
                columns = resolve_columns(header, statement, page, results=results)
            except PeriodsNotDetected:
                if statement != "notes":
                    raise
                columns = []
                continue
            last_text = None
            continue
        line = lines[i]
        i += 1
        text = line_text(line)
        words, row = split_row(line, columns) if columns else (line, {})
        if not row:
            if not rows:
                note_x = note_x or next((w["x0"] for w in line if _is_note(w)), None)
            if columns and statement != "notes" and _NOTES_BLOCK.match(text):
                break
            if columns and line[0]["x0"] >= columns[0].x0 - COLUMN_TOLERANCE:
                continue  # column sub-heading ("(Restated)") or margin text, not a heading
            if rows and last_text is None and _is_tail(rows[-1], line, text):
                rows[-1].label = _join(rows[-1].label, text)
                continue
            if last_text is not None and text[:1].islower():  # a heading wrapped onto two lines
                if section == line_text(last_text):
                    section = _join(section, text)
                last_text = last_text + line
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
            if last_text is not None and top - last_text[-1]["top"] < WRAP_FACTOR * height:
                label, wrapped = line_text(last_text), True
                section = before_heading
            else:
                label = section
        below = lines[i] if i < len(lines) else None
        if (
            all(w["text"] in _DASHES for w in row.values())
            and not _SUB_HEADING.match(label)
            and below is not None
            and below[0]["x0"] > line[0]["x0"] + INDENT
        ):
            # A heading printed with nil dashes ("Current assets - -"), items indented below it.
            last_text, before_heading, section = words, section, label
            continue
        if statement != "notes":
            _check_figures(label, row, page)
        rows.append(_Row(label, row, columns, section, note, top, height, wrapped))
        last_text = None
    found_values = [
        _value(r, column, r.values[column.key], page, statement, scope, unit)
        for r in rows
        for column in r.columns
    ]
    return found_values, columns


def _check_figures(label: str, row: dict[str, Word], page: int) -> None:
    """Raise E204 when a row's figures look misread: its label holds a figure (the page's figures
    do not line up with their labels), or a figure has an unpaired bracket (its sign is unknown)."""
    if _FIGURE_TEXT.search(label):
        raise ParseError(
            f"page {page}: the label {label[:60]!r} holds a figure; the figures do not line up "
            "with their labels"
        )
    for word in row.values():
        raw = word["text"]
        if raw.startswith("(") != raw.endswith(")"):
            raise ParseError(f"page {page}: figure {word['text']!r} has an unpaired bracket")


def _value(
    row: _Row,
    column: Column,
    word: Word,
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
) -> PdfValue:
    assert column.period is not None and column.months is not None
    value = parse_number(word["text"])
    return PdfValue(
        label=row.label,
        fiscal_year=column.fiscal_year,
        period=column.period,
        months=column.months,
        period_end=column.period_end,
        raw=word["text"],
        value=value,
        rupees=value * RUPEES_PER_UNIT[unit],
        unit=unit,
        page=page,
        bbox=(word["x0"], word["top"], word["x1"], word["bottom"]),
        statement=statement,
        scope=scope,
        section=row.section,
        note=row.note,
    )


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
