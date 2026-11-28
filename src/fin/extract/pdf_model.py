"""Constants, patterns and records of the PDF statement reader (see fin.extract.pdf)."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from fin.audit.errors import DocumentFailure
from fin.extract.periods import fiscal_year_of

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
    unit_given: bool = False  # the page names no unit: `unit` is the one the user gave


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
    unit_given: bool = False  # read in the unit the user gave (no unit line)
