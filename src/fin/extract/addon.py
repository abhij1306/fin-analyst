"""The optional PDF add-on: read the statement pages pdfplumber could not, with one chosen engine.

fin's pdfplumber extractor (`fin.extract.pdf`) stays the primary path. The user chooses at most one
engine (`fin parser use <name>`, `[pdf] parser` in config.toml): Docling (`docling_run`, local),
LiteParse (`liteparse_run`, local) or LlamaParse (`llamaparse_run`, cloud). Ingest
(`fin.extract.fallback`) hands the pages pdfplumber skipped to `read_skipped`:

1. The engine reads only those pages, once per document (`Request`; `subset` makes a PDF of just
   them). Its adapter gives each page's tables as `pdf_tables.Table` grids, boxes in fin's
   top-left convention, and the page's text lines (`PageRead`).
2. Each page's tables are read by `fin.extract.pdf_tables` with fin's own rules and guards, the
   same for every engine. The page's title scope, unit and whether it is a results statement come
   from pdfplumber's lines.
3. A page the engine cannot read either stays unread, with the engine's reason. The engine failing
   (E206) is logged and leaves the pages unread; it never fails the document.

A scanned PDF (no text layer, E104) of at most SCANNED_PAGE_LIMIT pages (a results filing) is read
by `read_scanned` when the engine's OCR is used (`Engine.OCR`: Docling and LlamaParse, not
LiteParse): the engine OCRs every page, statement pages are found by fin's title rules on its text,
and their tables are read the same way. A larger scanned PDF, or one the engine does not OCR, stays
E104.
"""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pypdfium2

from fin.audit.errors import DocumentFailure, FileUnreadable, PdfAddonFailed
from fin.extract.pdf_lines import (
    is_results,
    open_pdf,
    page_parts,
    scope_of,
    statement_of,
    unit_of,
)
from fin.extract.pdf_model import TITLE_LINES, Line, PdfValue, SkippedPage
from fin.extract.pdf_tables import PageContext, Table, read_tables, unit_of_text
from fin.extract.periods import STATEMENTS

# The engines by their [pdf] parser name (also their `read_by` and their uv extra).
ADDON_LABELS = {"docling": "Docling", "liteparse": "LiteParse", "llamaparse": "LlamaParse"}
SCANNED_PAGE_LIMIT = 40  # a scanned PDF this long or shorter (a results filing) is OCR'd
_NOT_A_NAME = re.compile(r"\b(?:notes?|statements?|reports?|schedules?)\b", re.I)


@dataclass(frozen=True)
class Request:
    path: Path  # the original PDF
    pages: list[int]  # 1-based pages of `path` to read, in order
    cache_dir: Path  # where the engine may keep its responses (LlamaParse, in the data folder)


@dataclass
class PageRead:
    """What the engine found on one page: its tables and text lines, or why it could not read it."""

    tables: list[Table] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    error: DocumentFailure | None = None


Found = dict[int, PageRead]  # by page number in the original document


class Engine(Protocol):
    """An engine module (`docling_run`, `liteparse_run`, `llamaparse_run`)."""

    NAME: str  # its [pdf] parser name, read_by value and uv extra
    LABEL: str
    MODULE: str  # the module `fin doctor` imports to check it is installed
    OCR: bool  # fin sends it scanned PDFs (False: its OCR is not trusted; they stay E104)

    def installed(self) -> bool: ...

    def unsupported(self) -> str | None:
        """Why it cannot run on this machine, or None."""
        ...

    def ready(self) -> tuple[str, str | None]:
        """(what `fin doctor` says about its models or key, the fix when something is missing)."""
        ...

    def download_models(self) -> None: ...

    def convert(self, request: Request, notices: list[str]) -> Found:
        """Read `request.pages`. Raises E206 (PdfAddonFailed) when the engine fails."""
        ...


@dataclass
class AddonResult:
    reader: str  # the engine's name: the read_by of `values`
    values: list[PdfValue] = field(default_factory=list)
    read_pages: list[int] = field(default_factory=list)
    unread: list[SkippedPage] = field(default_factory=list)  # still not read, with the reason
    notices: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (code, message)


def read_skipped(
    engine: Engine,
    path: Path,
    skipped: list[SkippedPage],
    start_month: int,
    cache_dir: Path,
    unit: str | None = None,
) -> AddonResult:
    """Read the statement pages pdfplumber skipped with `engine` (`unit`: the one the user gave,
    for a page that names none). Pages it reads give values; the rest are returned in `unread`,
    with the engine's reason when it tried."""
    result = AddonResult(engine.NAME)
    by_page: dict[int, list[SkippedPage]] = {}
    for page in skipped:
        by_page.setdefault(page.page, []).append(page)
    contexts: dict[int, PageContext] = {}
    for number, pages in by_page.items():
        statements = {p.statement for p in pages}
        if len(statements) > 1:  # two statements on one page (a spread): not told apart
            result.unread.extend(pages)
            continue
        lines = _page_lines(path, number)
        contexts[number] = _context(lines, number, pages[0], engine.LABEL, unit)
    if not contexts:
        return result
    request = Request(path, sorted(contexts), cache_dir)
    try:
        found = engine.convert(request, result.notices)
    except PdfAddonFailed as exc:
        result.warnings.append((exc.code, exc.message))
        result.unread.extend(p for n in contexts for p in _with_reason(by_page[n], exc, engine))
        return result
    for number, context in contexts.items():
        page = found.get(number, PageRead())
        try:
            if page.error is not None:
                raise page.error
            values = read_tables(context, page.tables, start_month)
        except DocumentFailure as exc:
            result.unread.extend(_with_reason(by_page[number], exc, engine))
            continue
        result.values.extend(values)
        result.read_pages.append(number)
    result.unread.sort(key=lambda s: s.page)
    if result.read_pages:
        pages = ", ".join(map(str, result.read_pages))
        result.notices.append(
            f"{len(result.values)} figures read by {engine.LABEL} from pages {pages} (pdfplumber "
            "could not read them)"
        )
    return result


def read_scanned(
    engine: Engine, path: Path, start_month: int, cache_dir: Path, unit: str | None = None
) -> AddonResult | None:
    """Read a scanned PDF's statement pages with the engine's OCR; None when it has more than
    SCANNED_PAGE_LIMIT pages (it stays E104)."""
    count = page_count(path)
    if count > SCANNED_PAGE_LIMIT:
        return None
    result = AddonResult(engine.NAME)
    request = Request(path, list(range(1, count + 1)), cache_dir)
    try:
        found = engine.convert(request, result.notices)
    except PdfAddonFailed as exc:
        result.warnings.append((exc.code, exc.message))
        return result
    for number, page in sorted(found.items()):
        title = _scanned_title(page.texts)
        if title is None:
            if page.error is not None:  # not read at all: which statement it holds is not known
                result.unread.append(SkippedPage(number, "unknown", page.error))
            continue
        statement = statement_of(title, numbered=True)
        assert statement is not None
        named = next((u for u in map(unit_of_text, page.texts) if u is not None), None)
        scope = scope_of(title, numbered=True)
        context = PageContext(
            number, statement, scope, named, is_results(title), engine.LABEL, given_unit=unit
        )
        try:
            if page.error is not None:
                raise page.error
            values = read_tables(context, page.tables, start_month)
        except DocumentFailure as exc:
            result.unread.append(SkippedPage(number, statement, exc))
            continue
        result.values.extend(values)
        result.read_pages.append(number)
    if result.read_pages:
        pages = ", ".join(map(str, result.read_pages))
        result.notices.append(
            f"scanned PDF: {len(result.values)} figures read by {engine.LABEL} (OCR) from pages "
            f"{pages}"
        )
    return result


def page_count(path: Path) -> int:
    try:
        document = pypdfium2.PdfDocument(path)
    except pypdfium2.PdfiumError as exc:
        raise FileUnreadable(f"{path.name}: not a readable PDF ({exc})") from exc
    count = len(document)
    document.close()
    return count


def subset(path: Path, pages: list[int]) -> io.BytesIO:
    """A PDF of only `pages` (1-based) of `path`, in memory: page i of it is `pages[i - 1]`."""
    source = pypdfium2.PdfDocument(path)
    copy = pypdfium2.PdfDocument.new()
    try:
        copy.import_pages(source, [p - 1 for p in pages])
        buffer = io.BytesIO()
        copy.save(buffer)
    finally:
        copy.close()
        source.close()
    buffer.seek(0)
    return buffer


def _scanned_title(texts: list[str]) -> list[Line] | None:
    """The title of a primary statement among a scanned page's first text blocks (fin's title
    rules), as a one-line page. OCR joins the company name and the title into one block, so the
    title may start after a name: capitalised words that are not "Notes", "Statement", "Report"
    ("Notes forming part of the Balance Sheet" is not a balance sheet). None for a page that is not
    a primary statement."""
    for text in texts[:TITLE_LINES]:
        words = text.split()
        for i in range(len(words)):
            name = words[:i]
            if not all(w[:1].isupper() or w[:1].isdigit() for w in name):
                break
            if _NOT_A_NAME.search(" ".join(name)):
                break
            line: list[Line] = [[{"text": " ".join(words[i:])}]]
            if statement_of(line, numbered=True) in STATEMENTS:
                return line
    return None


def _with_reason(
    pages: list[SkippedPage], exc: DocumentFailure, engine: Engine
) -> list[SkippedPage]:
    """The pages, still not read: pdfplumber's reason, then the engine's (with its code)."""
    return [
        SkippedPage(
            p.page, p.statement, type(exc)(f"{p.error.message}; {engine.LABEL}: {exc.message}")
        )
        for p in pages
    ]


def _page_lines(path: Path, number: int) -> list[Line]:
    """pdfplumber's lines of a page (both halves of a spread)."""
    with open_pdf(path) as pdf:
        return [line for part in page_parts(pdf.pages[number - 1]) for line in part]


def _context(
    lines: list[Line], number: int, page: SkippedPage, reader: str, given_unit: str | None
) -> PageContext:
    return PageContext(
        page=number,
        statement=page.statement,
        scope=scope_of(lines, numbered=True),
        unit=unit_of(lines),
        results=is_results(lines),
        reader=reader,
        given_unit=given_unit,
    )
