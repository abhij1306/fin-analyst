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
   each column's period ("FY25", "FY26-Q1", "FY26-H1") for the configured fiscal year start. A
   flow column that names no length is a year only when it ends on the fiscal year end (31 March
   by default) and the page is not a results statement; otherwise the page
   is skipped (E202), as it is when two columns hold the same period (such as standalone and
   consolidated printed side by side).
4. A line whose trailing numbers sit under every period column is a value row. So is a line with
   figures under some columns and nothing under the others (a new item with no comparative): the
   figures printed load, a blank cell is no value; one with plain integers loads only when a
   full row below it shows it is in the table body. A line with a figure fin cannot place under
   its columns makes the page misread (E204), never a heading. The words to their left are the
   label, minus a note reference when the header has a "Note" column. A row with no
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
    DataFolderNotWritable,
    DocumentFailure,
    FileEncrypted,
    FileUnreadable,
    NoTablesFound,
    ParseError,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)
from fin.extract.periods import (
    FY_START_MONTH,
    MONTHS,
    PHRASE,
    STATEMENTS,
    fiscal_year_of,
    months_in,
    period_of,
    resolve_months,
)
from fin.extract.units import NIL, parse_figure, to_rupees, unit_in

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
FIGURE_TEXT = re.compile(r"\d[,.]\d")  # a figure with a comma or decimal point, not a page number
LABEL_LINES = 4  # how many lines above a column header can hold period-length labels

_ORDINAL = r"(?:st|nd|rd|th)?"
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})\b")  # 31.03.2025
_DAY_FIRST = re.compile(  # 31 March 2025, 31-Mar-2025, 30-Sep-25
    rf"\b(\d{{1,2}}){_ORDINAL}[\s,.-]*([a-z]{{3,9}})\.?[\s,.-]*(\d{{4}}|\d{{2}})\b", re.I
)
_MONTH_FIRST = re.compile(rf"\b([a-z]{{3,9}})\.?\s*(\d{{1,2}}){_ORDINAL}\s*,?\s*(\d{{4}})\b", re.I)
_FY_RANGE = re.compile(r"\b(\d{4})\s*[-–]\s*(\d{2}|\d{4})\b")  # 2023-24 or 2023-2024
_NUMBER = re.compile(r"^\(?-?(?:[\d,]*\d(?:\.\d+)?|\.\d+)\)?$")
_DASH = re.compile(r"^[-–—]$")  # a nil figure printed as a dash
_YEAR = re.compile(r"^(19|20)\d\d$")
_NOTE_REF = re.compile(r"^\d{1,3}(\.\d{1,2})?[a-z]?(\([a-z0-9]{1,4}\))*$|^&$|^and$", re.I)
SUB_HEADING = re.compile(r"^\(?\s*[a-z]{1,4}\s*\)", re.I)  # "(a)", "b)", "(iii)", "(c )"
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
    error: DocumentFailure  # E201 (no row, unrecognised title), E202, E203, E204 (misread)

    def as_dict(self) -> dict[str, object]:
        return {
            "page": self.page,
            "statement": self.statement,
            "code": self.error.code,
            "reason": self.error.message,
        }


@dataclass(frozen=True)
class PdfExtraction:
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
    def key(self) -> str:
        """Tells apart the columns of one header (two can end on the same date)."""
        return f"{self.period_end.isoformat()}|{self.months}"

    @property
    def label(self) -> str:
        """The column's name in a Markdown table: 'FY25', or its length and date."""
        return self.period or (
            fiscal_year_of(self.period_end)
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


def extract_pdf(path: Path, start_month: int = FY_START_MONTH) -> PdfExtraction:
    """Extract every readable statement value from a text PDF. Periods are named for a fiscal year
    starting in `start_month` (config `fiscal_year_start_month`).

    Raises E101/E102/E104, and E201/E202/E203 when no statement page can be read at all.
    """
    with open_pdf(path) as pdf:
        parts = [(page.page_number, lines) for page in pdf.pages for lines in page_parts(page)]

    results = any(is_results(lines) for _, lines in parts)
    values: list[PdfValue] = []
    skipped: list[SkippedPage] = []
    previous: _Page | None = None
    for number, lines in parts:
        previous = _read_one_part(
            number, lines, previous, values, skipped, results=results, start_month=start_month
        )
    if not any(v.statement in STATEMENTS for v in values):
        raise _nothing_read(path, skipped)
    return PdfExtraction(values, skipped)


def _read_one_part(
    number: int,
    lines: list[Line],
    previous: _Page | None,
    values: list[PdfValue],
    skipped: list[SkippedPage],
    *,
    results: bool,
    start_month: int,
) -> _Page | None:
    """Read one page part: add its values to `values`, or record why it was skipped in `skipped`.
    Returns the page as read, for the next part to carry on from (None when there is none)."""
    statement = statement_of(lines, numbered=results)
    scope = scope_of(lines, numbered=results)
    if statement is None and previous is not None and _continues(previous, number, lines):
        statement, scope = previous.statement, previous.scope
        if previous.failed is not None:  # carries on a page that was not read: nor is it
            _skip_unread_carry_on(previous, number, lines, skipped)
            return None
    if statement is None:
        untitled = _untitled_statement(lines, number)
        if untitled is not None:
            skipped.append(SkippedPage(number, *untitled))
        return None
    continues = _carried_from(previous, number, statement, scope)
    try:
        page_values, page = _read_part(
            lines,
            number,
            statement,
            scope,
            continues,
            results=is_results(lines) or bool(continues and results),
            start_month=start_month,
        )
    except (UnitNotDetected, PeriodsNotDetected, NoTablesFound, ParseError) as exc:
        if statement != "notes":
            skipped.append(SkippedPage(number, statement, exc))
        return _Page(number, statement, scope, "", [], exc)
    values.extend(page_values)
    return page


def _skip_unread_carry_on(
    previous: _Page, number: int, lines: list[Line], skipped: list[SkippedPage]
) -> None:
    """Record a page part that carries on a page that was not read (when it holds figures)."""
    if previous.failed is not None and _figure_lines(lines) >= UNREAD_FIGURE_LINES:
        reason = f"page {number}: carries on page {previous.number}, which was not read"
        skipped.append(SkippedPage(number, previous.statement, type(previous.failed)(reason)))


def _carried_from(
    previous: _Page | None, number: int, statement: str, scope: str | None
) -> _Page | None:
    """The page before, when it was read and this part carries on its statement; else None."""
    if previous is None or previous.failed is not None:
        return None
    if (previous.statement, previous.scope) != (statement, scope):
        return None
    return previous if number - previous.number <= 1 else None


def _nothing_read(path: Path, skipped: list[SkippedPage]) -> DocumentFailure:
    """The error for a PDF where no statement page could be read."""
    if not skipped:
        return NoTablesFound(f"{path.name}: no statement tables with values found")
    reasons = "; ".join(s.error.message for s in skipped[:5])
    more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
    error = type(skipped[0].error)(f"{path.name}: no statement page could be read: {reasons}{more}")
    error.pages = tuple(s.as_dict() for s in skipped)
    return error


def _read_part(
    lines: list[Line],
    number: int,
    statement: str,
    scope: str | None,
    continues: _Page | None,
    *,
    results: bool,
    start_month: int,
) -> tuple[list[PdfValue], _Page]:
    """The values of one statement page part, and the page as read. `continues`: the page before,
    when this part carries on its statement (its unit and columns hold until replaced). Raises
    E203 (no unit), E202 (no period columns), E201 (no row of figures on a primary statement) or
    E204."""
    unit = unit_of(lines) or (continues.unit if continues else None)
    if unit is None:
        raise UnitNotDetected(f"page {number}: no unit line such as '₹ in Lakhs'")
    page_values, columns = read_page(
        lines,
        number,
        statement,
        scope,
        unit,
        continues.columns if continues else [],
        results=results,
        start_month=start_month,
    )
    if not columns:
        raise PeriodsNotDetected(
            f"page {number}: no period column headers (such as 'As at 31 March 2025')"
        )
    if not page_values and statement in STATEMENTS and not continues:
        raise NoTablesFound(
            f"page {number}: titled as a statement, but no line has a figure under a period column"
        )
    return page_values, _Page(number, statement, scope, unit, columns)


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
    return sum(1 for line in lines if _is_value_like(line) and FIGURE_TEXT.search(line[-1]["text"]))


def _named_statement(lines: list[Line]) -> tuple[str, str] | None:
    """(statement, line) for the first top line (of NAMED_LINES, above the first figure) that
    reads as a title naming a primary statement within its first TITLE_WORDS words, or None. Not
    a title: a line starting in lower case or ending in a figure, '.' or ':' (a sentence, a table
    row, a lead-in)."""
    for line in lines[:NAMED_LINES]:
        text = line_text(line).strip()
        if _is_value_like(line) and FIGURE_TEXT.search(line[-1]["text"]):
            break
        if not _could_be_title(line, text):
            continue
        match = _STATEMENT_WORDS.search(text)
        if match and len(text[: match.start()].split()) < TITLE_WORDS:
            return _statement_named(match.group(0).lower()), text
    return None


def _could_be_title(line: Line, text: str) -> bool:
    """False for an empty line, one starting in lower case, a table row, or one ending in '.' or
    ':' (a sentence or a lead-in)."""
    if not text or text[0].islower() or _is_value_like(line):
        return False
    return not text.rstrip(" -").endswith((".", ":"))


def _statement_named(words: str) -> str:
    """The primary statement that title words (in lower case) name."""
    if "sheet" in words or "assets" in words:
        return "balance_sheet"
    return "cash_flow" if "cash" in words else "profit_and_loss"


def _continues(previous: _Page, number: int, lines: list[Line]) -> bool:
    """True when an untitled page part carries on the statement just read: the next page part
    (such as the second half of a balance sheet printed over two half pages), with no other title
    (statement of changes in equity, notes, a report, or words naming another statement) at its
    top."""
    if previous.statement not in STATEMENTS or number - previous.number > 1:
        return False
    if any(_OTHER_TITLE.search(line_text(line)) for line in lines[:TITLE_LINES]):
        return False
    named = _named_statement(lines)
    return named is None or named[0] == previous.statement


def open_pdf(path: Path, *, text_layer: bool = True) -> Any:
    """Open a PDF with pdfplumber. Raises E101 (unreadable), E102 (encrypted), and E104 (scanned)
    unless `text_layer` is False (an evidence crop of a page Docling read by OCR)."""
    try:
        pdf = pdfplumber.open(path)
    except PdfminerException as exc:
        if isinstance(exc.args[0] if exc.args else None, PDFEncryptionError):
            raise FileEncrypted(f"{path.name}: the PDF is password protected") from exc
        raise FileUnreadable(f"{path.name}: not a readable PDF ({exc})") from exc
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc
    if text_layer and not any(page.chars for page in pdf.pages):
        pdf.close()
        raise ScannedPdf(f"{path.name}: no text layer")
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
    """(statement, title match) from the page's top lines. `numbered`: first look for a primary
    statement titled as a numbered note ("1. Balance Sheet as on")."""
    if numbered:
        found = _numbered_title(lines)
        if found is not None:
            return found
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        for statement, title in STATEMENT_TITLES:
            match = title.search(text)
            if match:
                return statement, match
    return None


def _numbered_title(lines: list[Line]) -> tuple[str, re.Match[str]] | None:
    """(statement, title match) for a primary statement titled as a numbered note, or None."""
    for line in lines[:TITLE_LINES]:
        text = line_text(line).strip()
        if not _NUMBERED.match(text):
            continue
        for statement, title in STATEMENT_TITLES:
            match = title.search(_NUMBERED.sub("", text))
            if match and statement in STATEMENTS:
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


def is_results(lines: list[Line]) -> bool:
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
            month = MONTHS.get(match.group(month_group).lower())
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


def _is_number(text: str) -> bool:
    """A figure ("1,234.56", "(56)", ".82") or a nil dash."""
    return bool(_NUMBER.match(text) or _DASH.match(text))


def _is_value_like(line: Line) -> bool:
    """True when a line ends in a figure (not a bare year), so it cannot be part of a header."""
    last = line[-1]["text"]
    return _is_number(last) and not _YEAR.match(last)


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


def header_starting(lines: list[Line], i: int) -> tuple[list[Column], int] | None:
    """The column header that starts at line i, with its period lengths (`complete_header`), and
    the index of the line after it; None when no header starts there. A header found at i that
    ends where the one found at i + 1 ends starts at i + 1 instead (line i is a title above it)."""
    found = header_at(lines, i)
    if found is None:
        return None
    later = header_at(lines, i + 1)
    if later is not None and later[1] == found[1]:
        return None
    return complete_header(lines, i, found[1])


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


def split_row(line: Line, columns: list[Column]) -> tuple[Line, dict[str, Word]]:
    """Split a line into its leading words and its values {column key: word}.

    Values are the trailing number words, each under a different period column. A column owns the
    band from halfway to its left neighbour to halfway to its right one; the first starts
    COLUMN_TOLERANCE left of its header and the last runs to the page edge. A line is a value row
    when every column has a value, or some do and it is plainly a row (`_is_partial_row`); a column
    with no value is left out. Otherwise the values are {}.
    """
    cut, values = _trailing_values(line, columns)
    if len(values) < len(columns) and not _is_partial_row(line[:cut], values, columns):
        return line, {}
    return line[:cut], {c.key: values[c.key] for c in columns if c.key in values}


def _trailing_values(line: Line, columns: list[Column]) -> tuple[int, dict[str, Word]]:
    """The index where a line's trailing number words start, and those words {column key: word},
    each under a different column (see `split_row`)."""
    values: dict[str, Word] = {}
    cut = len(line)
    for i in range(len(line) - 1, -1, -1):
        word = line[i]
        if not _is_number(word["text"]):
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
    return cut, values


def _is_partial_row(
    label: Line, values: dict[str, Word], columns: list[Column], *, integers: bool = False
) -> bool:
    """True for a line with figures under some columns and nothing printed under the others: it
    has a label of its own, wholly left of the columns, and each figure has a comma or decimal
    point or is a nil dash (so a page number or a registration number is not a row). `integers`:
    plain integers count too (`_read_line` keeps such a row only inside the table body)."""
    if not values or not label:
        return False
    if max(w["x1"] for w in label) >= columns[0].x0 - COLUMN_TOLERANCE:
        return False
    return integers or all(
        FIGURE_TEXT.search(w["text"]) or _DASH.match(w["text"]) for w in values.values()
    )


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
    columns: list[Column], statement: str, page: int, *, results: bool, start_month: int
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
        months = resolve_months(
            statement, column.period_end, column.months, start_month, where=where
        )
        period = period_of(column.period_end, months, start_month, where=where)
        resolved.append(replace(column, months=months, period=period))
    names = [c.period for c in resolved]
    if len(set(names)) < len(names):
        listed = ", ".join(c.period for c in resolved if c.period)
        raise PeriodsNotDetected(f"{where}: two columns hold the same period ({listed})")
    return resolved


@dataclass
class _Reader:
    """What reading a page has found so far, top to bottom."""

    rows: list[_Row] = field(default_factory=list)
    note_x: float | None = None  # left edge of the "Note" column heading
    section: str = ""  # the heading the next row sits under
    before_heading: str = ""  # the section before the last heading line, to undo a wrapped label
    last_text: Line | None = None  # the line just read, when it was a text line
    # Partial rows of plain integers, kept once a full row below shows they are in the table body
    # (a page number or footer below the table is not a row).
    pending: list[_Row] = field(default_factory=list)


def read_page(
    lines: list[Line],
    page: int,
    statement: str,
    scope: str | None,
    unit: str,
    columns: list[Column],
    *,
    results: bool = False,
    start_month: int = FY_START_MONTH,
) -> tuple[list[PdfValue], list[Column]]:
    """Values of one statement page, and the columns in force at its end ([] if none).

    `columns` are the columns carried over from the page before (a continued statement).
    `results`: the page is a quarterly or half-year results statement, so a flow column must name
    its length. Raises PeriodsNotDetected (E202) when a header's periods cannot be told.
    """
    state = _Reader()
    i = 0
    while i < len(lines):
        found = header_starting(lines, i)
        if found is not None:
            header, end = found
            state.note_x = state.note_x or _note_x([w for ln in lines[i:end] for w in ln])
            i = end
            columns = _header_columns(
                state, header, statement, page, results=results, start_month=start_month
            )
            continue
        below = lines[i + 1] if i + 1 < len(lines) else None
        if _read_line(state, lines[i], below, columns, statement, page):
            break
        i += 1
    found_values = [
        _value(r, column, r.values[column.key], page, statement, scope, unit, start_month)
        for r in state.rows
        for column in r.columns
        if column.key in r.values
    ]
    return found_values, columns


def _header_columns(
    state: _Reader,
    header: list[Column],
    statement: str,
    page: int,
    *,
    results: bool,
    start_month: int,
) -> list[Column]:
    """The columns a header row names. On a notes page, a header whose periods cannot be told
    gives no columns ([]) rather than raising PeriodsNotDetected (E202)."""
    try:
        columns = resolve_columns(header, statement, page, results=results, start_month=start_month)
    except PeriodsNotDetected:
        if statement != "notes":
            raise
        return []
    state.last_text = None
    return columns


def _read_line(
    state: _Reader,
    line: Line,
    below: Line | None,
    columns: list[Column],
    statement: str,
    page: int,
) -> bool:
    """Take in one line that is not a header: a row of figures, a heading or text. `below`: the
    next line, if any. True when the line ends the table (a "Notes:" line)."""
    words, row = split_row(line, columns) if columns else (line, {})
    pending = _integer_row(line, columns) if columns and not row else None
    if pending is not None:
        words, row = pending
    elif not row:
        if columns and _unplaced_figures(line, columns, statement, page):
            state.last_text = None
            return False
        return _text_line(state, line, columns, statement)
    words, note = _strip_note(words, state.note_x)
    height = max(w["bottom"] - w["top"] for w in line)
    label, wrapped = _row_label(state, words, line, height)
    if _is_nil_heading(row, label, line, below):
        # A heading printed with nil dashes ("Current assets - -"), items indented below it.
        state.last_text, state.before_heading, state.section = words, state.section, label
        return False
    new = _Row(label, row, columns, state.section, note, line[0]["top"], height, wrapped)
    state.last_text = None
    if pending is not None:
        state.pending.append(new)
        return False
    kept = [new]
    if len(row) == len(columns):  # a full row: the integer rows above it are in the table body
        kept = [*state.pending, new]
        state.pending.clear()
    for r in kept if statement != "notes" else []:
        _check_figures(r.label, r.values, page)
    state.rows = sorted([*state.rows, *kept], key=lambda r: r.top)
    return False


def _integer_row(line: Line, columns: list[Column]) -> tuple[Line, dict[str, Word]] | None:
    """(label words, values) for a line with a label of its own and plain integers under some
    columns (`_is_partial_row` with integers); None otherwise."""
    cut, values = _trailing_values(line, columns)
    if not _is_partial_row(line[:cut], values, columns, integers=True):
        return None
    return line[:cut], values


def _unplaced_figures(line: Line, columns: list[Column], statement: str, page: int) -> bool:
    """True for a line that is not a row but ends in figures (with a comma or decimal point) under
    the columns: never a heading. On a statement page fin cannot tell which column they belong
    to, so it raises E204 rather than guess; on a notes page the line is passed over."""
    found = _trailing_values(line, columns)[1]
    if not any(FIGURE_TEXT.search(w["text"]) for w in found.values()):
        return False
    if statement != "notes":
        raise ParseError(
            f"page {page}: the figures of {line_text(line)[:60]!r} cannot be placed under the "
            "period columns"
        )
    return True


def _is_nil_heading(row: dict[str, Word], label: str, line: Line, below: Line | None) -> bool:
    """True for a row whose figures are all nil dashes, with the next line indented below it."""
    if not all(w["text"] in NIL for w in row.values()) or SUB_HEADING.match(label):
        return False
    return below is not None and below[0]["x0"] > line[0]["x0"] + INDENT


def _note_x(words: Line) -> float | None:
    """The left edge of the first 'Note' column heading word among `words`, or None."""
    return next((w["x0"] for w in words if _is_note(w)), None)


def _text_line(state: _Reader, line: Line, columns: list[Column], statement: str) -> bool:
    """Take in a line with no row of figures: a heading, the tail of a wrapped label or heading, or
    text in the figure columns (ignored). True when it is a "Notes:" line that ends the table."""
    text = line_text(line)
    if not state.rows:
        state.note_x = state.note_x or _note_x(line)
    if columns and statement != "notes" and _NOTES_BLOCK.match(text):
        return True
    if columns and line[0]["x0"] >= columns[0].x0 - COLUMN_TOLERANCE:
        return False  # column sub-heading ("(Restated)") or margin text, not a heading
    if state.rows and state.last_text is None and _is_tail(state.rows[-1], line, text):
        state.rows[-1].label = _join(state.rows[-1].label, text)
        return False
    if state.last_text is not None and text[:1].islower():  # a heading wrapped onto two lines
        if state.section == line_text(state.last_text):
            state.section = _join(state.section, text)
        state.last_text = state.last_text + line
        return False
    state.last_text = line
    if _WORDY.search(text):
        state.before_heading = state.section
        if not SUB_HEADING.match(text):
            state.section = text
    return False


def _strip_note(words: Line, note_x: float | None) -> tuple[Line, str | None]:
    """A row's label words without the note reference at their end (under the "Note" column at
    `note_x`), and that reference."""
    if note_x is None:
        return words, None
    words = list(words)
    notes: list[str] = []
    while words and _NOTE_REF.match(words[-1]["text"]):
        if words[-1]["x0"] < note_x - COLUMN_TOLERANCE:
            break
        notes.insert(0, words.pop()["text"])
    return words, " ".join(notes) or None


def _row_label(state: _Reader, words: Line, line: Line, height: float) -> tuple[str, bool]:
    """A row's label, and whether it was taken from the line above. A row with no label words
    takes the text line just above it (a label wrapped around its figures), or else the heading it
    sits under (an unlabelled subtotal)."""
    label = line_text(words)
    if label:
        return label, False
    if state.last_text is not None and line[0]["top"] - state.last_text[-1]["top"] < (
        WRAP_FACTOR * height
    ):
        state.section = state.before_heading
        return line_text(state.last_text), True
    return state.section, False


def _check_figures(label: str, row: dict[str, Word], page: int) -> None:
    """Raise E204 when a row's figures look misread: its label holds a figure (the page's figures
    do not line up with their labels), or a figure has an unpaired bracket (its sign is unknown)."""
    if FIGURE_TEXT.search(label):
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
    start_month: int,
) -> PdfValue:
    assert column.period is not None and column.months is not None
    value = parse_figure(word["text"])
    assert value is not None  # split_row takes only figures
    return PdfValue(
        label=row.label,
        fiscal_year=fiscal_year_of(column.period_end, start_month),
        period=column.period,
        months=column.months,
        period_end=column.period_end,
        raw=word["text"],
        value=value,
        rupees=to_rupees(value, unit),
        unit=unit,
        page=page,
        bbox=(word["x0"], word["top"], word["x1"], word["bottom"]),
        statement=statement,
        scope=scope,
        section=row.section,
        note=row.note,
    )


CROP_MARGIN = 24.0  # points above and below the value in an evidence crop
CROP_RESOLUTION = 150  # dpi of an evidence crop


def render_crop(path: Path, page: int, bbox: tuple[float, float, float, float], out: Path) -> Path:
    """Write a PNG of the full-width band around `bbox` on `page` (1-based), bbox highlighted.

    The band spans the page width so the row label shows next to the value. E003 when the PNG
    cannot be written.
    """
    with open_pdf(path, text_layer=False) as pdf:
        p = pdf.pages[page - 1]
        _, top, _, bottom = bbox
        region = (
            0.0,
            max(0.0, top - CROP_MARGIN),
            float(p.width),
            min(float(p.height), bottom + CROP_MARGIN),
        )
        image = p.crop(region).to_image(resolution=CROP_RESOLUTION)
        image.draw_rect(bbox, stroke=(220, 0, 0), fill=(255, 230, 0, 60), stroke_width=2)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            image.save(out)
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot write {out}: {exc}") from exc
    return out
