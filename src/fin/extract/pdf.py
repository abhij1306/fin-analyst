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
the same statement and scope. A statement page with no unit line of its own is read in the unit the
user gave (`unit`; its values have `unit_given`), else skipped with E203: ingest then holds the
document with the question "which unit?". fin does not guess a unit. A page that still cannot be
read is skipped and reported in `PdfExtraction.skipped`; the document fails only when no statement
page can be read. So is a page whose figures look misread: a row label that holds a figure (the
figures do not line up with their labels) or a figure with an unpaired bracket (its sign is
unknown). fin reads clean digital statements; a page it skips is listed for a Claude reading, never
loaded wrong.

Every value keeps its raw text, the parsed `Decimal` in document units, the same in rupees, the
1-based page number and the word's bbox `(x0, top, x1, bottom)` in PDF points from the page's
top-left corner (pdfplumber's convention, so `page.crop(bbox)` shows it).
"""

from dataclasses import replace
from pathlib import Path

from fin.audit.errors import (
    DocumentFailure,
    NoTablesFound,
    ParseError,
    PeriodsNotDetected,
    UnitNotDetected,
)
from fin.extract.diagnostics import top_lines
from fin.extract.pdf_columns import _is_value_like
from fin.extract.pdf_lines import (
    is_results,
    line_text,
    open_pdf,
    page_parts,
    scope_of,
    statement_of,
    unit_of,
)
from fin.extract.pdf_model import (
    _OTHER_TITLE,
    _STATEMENT_WORDS,
    FIGURE_TEXT,
    NAMED_LINES,
    TITLE_LINES,
    TITLE_WORDS,
    UNREAD_FIGURE_LINES,
    Line,
    PdfExtraction,
    PdfValue,
    SkippedPage,
    _Page,
)
from fin.extract.pdf_reader import read_page
from fin.extract.periods import FY_START_MONTH, STATEMENTS


def extract_pdf(
    path: Path, start_month: int = FY_START_MONTH, unit: str | None = None
) -> PdfExtraction:
    """Extract every readable statement value from a text PDF. Periods are named for a fiscal year
    starting in `start_month` (config `fiscal_year_start_month`); `unit` is the one the user gave
    for pages with no unit line.

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
            number,
            lines,
            previous,
            values,
            skipped,
            results=results,
            start_month=start_month,
            unit=unit,
        )
    if not any(v.statement in STATEMENTS for v in values):
        raise _nothing_read(path, skipped, parts)
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
    unit: str | None,
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
            given_unit=unit,
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


def _nothing_read(
    path: Path, skipped: list[SkippedPage], parts: list[tuple[int, list[Line]]]
) -> DocumentFailure:
    """The error for a PDF where no statement page could be read. Its context holds what was seen:
    the top lines of the first pages when none is titled as a statement, else each skipped page."""
    if not skipped:
        tops = [
            {"page": number, "lines": top_lines(map(line_text, lines), 4)}
            for number, lines in parts[:6]
        ]
        return NoTablesFound(f"{path.name}: no statement tables with values found").with_context(
            pages=len({number for number, _ in parts}), tops=tops
        )
    reasons = "; ".join(s.error.message for s in skipped[:5])
    more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
    error = type(skipped[0].error)(f"{path.name}: no statement page could be read: {reasons}{more}")
    error.pages = tuple(s.as_dict() for s in skipped)
    error.context = {"skipped": [{"page": s.page, **(s.error.context or {})} for s in skipped[:10]]}
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
    given_unit: str | None,
) -> tuple[list[PdfValue], _Page]:
    """The values of one statement page part, and the page as read. `continues`: the page before,
    when this part carries on its statement (its unit and columns hold until replaced). Raises
    E203 (no unit), E202 (no period columns), E201 (no row of figures on a primary statement) or
    E204."""
    unit = unit_of(lines) or (continues.unit if continues else None)
    given = unit is None or bool(continues and continues.unit_given and not unit_of(lines))
    unit = unit or given_unit
    if unit is None:
        raise UnitNotDetected(f"page {number}: no unit line such as '₹ in Lakhs'").with_context(
            lines=top_lines(map(line_text, lines))
        )
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
    if given:
        page_values = [replace(v, unit_given=True) for v in page_values]
    return page_values, _Page(number, statement, scope, unit, columns, unit_given=given)


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
