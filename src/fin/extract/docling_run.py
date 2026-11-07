"""Docling, the optional PDF add-on: read the statement pages pdfplumber could not.

fin's pdfplumber extractor (`fin.extract.pdf`) stays the primary path. When the add-on is
installed (`./install.sh --docling`, `[pdf] docling = true` in config.toml), ingest hands the pages
it skipped to `read_skipped`:

1. One PDF of only those pages (pypdfium2) goes through Docling once: layout, table structure and
   OCR (Docling's defaults). The converter is made once per process and reused, so a batch ingest
   loads the models once.
2. Each page's tables are read by `fin.extract.docling_pages` with fin's own rules. The page's
   title scope, unit and whether it is a results statement come from pdfplumber's lines.
3. A page Docling cannot read either stays unread, with Docling's reason. Docling failing on the
   pages (E206) is logged and leaves them unread; it never fails the document.

A scanned PDF (no text layer, E104) of at most SCANNED_PAGE_LIMIT pages (a results filing) is read
by `read_scanned`: Docling OCRs every page, statement pages are found by fin's title rules on
Docling's text, and their tables are read the same way. A larger scanned PDF stays E104.

Docling downloads its models (about 700 MB) into `models_dir()` at install
(`docling-tools models download layout tableformer rapidocr`); if they are missing at first use,
`read_skipped` / `read_scanned` download them there and say so in a notice.
"""

import importlib.util
import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2

from fin.audit.errors import DoclingFailed, DocumentFailure, FileUnreadable
from fin.extract.docling_pages import (
    Cell,
    PageContext,
    Table,
    read_tables,
    top_left,
    unit_of_text,
)
from fin.extract.pdf import (
    TITLE_LINES,
    Line,
    PdfValue,
    SkippedPage,
    is_results,
    open_pdf,
    page_parts,
    scope_of,
    statement_of,
    unit_of,
)
from fin.extract.periods import STATEMENTS

SCANNED_PAGE_LIMIT = 40  # a scanned PDF this long or shorter (a results filing) is OCR'd
# The models fin's Docling pipeline uses, as `docling-tools models download` names their folders.
MODEL_FOLDERS = (
    "docling-project--docling-layout-heron",
    "docling-project--docling-models",
    "RapidOcr",
)
MODEL_DOWNLOAD = "uv run docling-tools models download layout tableformer rapidocr"
# Docling and the libraries under it log every model load; fin logs its own outcome instead.
_QUIET_LOGGERS = ("docling", "docling_core", "docling_ibm_models", "rapidocr", "RapidOCR")
_QUIET_ENV = {
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "TRANSFORMERS_VERBOSITY": "error",
    "TQDM_DISABLE": "1",
}
_NOT_A_NAME = re.compile(r"\b(?:notes?|statements?|reports?|schedules?)\b", re.I)
_converter: Any = None  # made on first use, reused for every document of the process


@dataclass
class DoclingResult:
    values: list[PdfValue] = field(default_factory=list)
    read_pages: list[int] = field(default_factory=list)
    unread: list[SkippedPage] = field(default_factory=list)  # still not read, with the reason
    notices: list[str] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)  # (code, message)


def installed() -> bool:
    """True when the Docling package can be imported (the add-on is installed)."""
    return importlib.util.find_spec("docling") is not None


def models_dir() -> Path:
    """Where `docling-tools models download` puts the models (DOCLING_CACHE_DIR/models, by
    default ~/.cache/docling/models)."""
    cache = os.environ.get("DOCLING_CACHE_DIR")
    return (Path(cache) if cache else Path.home() / ".cache" / "docling") / "models"


def models_present() -> bool:
    folder = models_dir()
    return all((folder / name).is_dir() for name in MODEL_FOLDERS)


def read_skipped(path: Path, skipped: list[SkippedPage], start_month: int) -> DoclingResult:
    """Read the statement pages pdfplumber skipped with Docling. Pages it reads give values; the
    rest are returned in `unread`, with Docling's reason when it tried."""
    result = DoclingResult()
    by_page: dict[int, list[SkippedPage]] = {}
    for page in skipped:
        by_page.setdefault(page.page, []).append(page)
    contexts: dict[int, PageContext] = {}
    for number, pages in by_page.items():
        statements = {p.statement for p in pages}
        if len(statements) > 1:  # two statements on one page (a spread): not told apart
            result.unread.extend(pages)
            continue
        contexts[number] = _context(_page_lines(path, number), number, pages[0].statement)
    if not contexts:
        return result
    try:
        found = _convert(path, sorted(contexts), result)
    except DoclingFailed as exc:
        result.warnings.append((exc.code, exc.message))
        result.unread.extend(p for n in contexts for p in _with_reason(by_page[n], exc))
        return result
    for number, context in contexts.items():
        try:
            values = read_tables(context, found.get(number, ([], []))[0], start_month)
        except DocumentFailure as exc:
            result.unread.extend(_with_reason(by_page[number], exc))
            continue
        result.values.extend(values)
        result.read_pages.append(number)
    result.unread.sort(key=lambda s: s.page)
    if result.read_pages:
        pages = ", ".join(map(str, result.read_pages))
        result.notices.append(
            f"{len(result.values)} figures read by Docling from pages {pages} (pdfplumber could "
            "not read them); unverified until `fin verify` with a reading agrees"
        )
    return result


def read_scanned(path: Path, start_month: int) -> DoclingResult | None:
    """Read a scanned PDF's statement pages with Docling's OCR; None when it has more than
    SCANNED_PAGE_LIMIT pages (it stays E104)."""
    try:
        document = pypdfium2.PdfDocument(path)
    except pypdfium2.PdfiumError as exc:
        raise FileUnreadable(f"{path.name}: not a readable PDF ({exc})") from exc
    count = len(document)
    document.close()
    if count > SCANNED_PAGE_LIMIT:
        return None
    result = DoclingResult()
    try:
        found = _convert(path, list(range(1, count + 1)), result)
    except DoclingFailed as exc:
        result.warnings.append((exc.code, exc.message))
        return result
    for number, (tables, texts) in sorted(found.items()):
        title = _scanned_title(texts)
        if title is None:
            continue
        statement = statement_of(title, numbered=True)
        assert statement is not None
        unit = next((u for u in map(unit_of_text, texts) if u is not None), None)
        scope = scope_of(title, numbered=True)
        context = PageContext(number, statement, scope, unit, is_results(title))
        try:
            values = read_tables(context, tables, start_month)
        except DocumentFailure as exc:
            result.unread.append(SkippedPage(number, statement, exc))
            continue
        result.values.extend(values)
        result.read_pages.append(number)
    if result.read_pages:
        pages = ", ".join(map(str, result.read_pages))
        result.notices.append(
            f"scanned PDF: {len(result.values)} figures read by Docling (OCR) from pages {pages}; "
            "unverified until `fin verify` with a reading agrees"
        )
    return result


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


def _with_reason(pages: list[SkippedPage], exc: DocumentFailure) -> list[SkippedPage]:
    """The pages, still not read: pdfplumber's reason, then Docling's (with Docling's code)."""
    return [
        SkippedPage(p.page, p.statement, type(exc)(f"{p.error.message}; Docling: {exc.message}"))
        for p in pages
    ]


def _page_lines(path: Path, number: int) -> list[Line]:
    """pdfplumber's lines of a page (both halves of a spread)."""
    with open_pdf(path) as pdf:
        return [line for part in page_parts(pdf.pages[number - 1]) for line in part]


def _context(lines: list[Line], number: int, statement: str) -> PageContext:
    return PageContext(
        page=number,
        statement=statement,
        scope=scope_of(lines, numbered=True),
        unit=unit_of(lines),
        results=is_results(lines),
    )


def _convert(
    path: Path, pages: list[int], result: DoclingResult
) -> dict[int, tuple[list[Table], list[str]]]:
    """Run Docling once on `pages` of `path`: {page: (tables, text lines)}. Raises E206."""
    converter = _get_converter(result)
    buffer = _subset(path, pages)
    from docling.datamodel.base_models import ConversionStatus, DocumentStream
    from docling.exceptions import ConversionError

    try:
        converted = converter.convert(
            DocumentStream(name=path.name, stream=buffer), raises_on_error=False
        )
    except (ConversionError, RuntimeError, ValueError, OSError, MemoryError) as exc:
        raise DoclingFailed(f"{path.name}: Docling failed on pages {pages}: {exc}") from exc
    if converted.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = "; ".join(e.error_message for e in converted.errors) or str(converted.status)
        raise DoclingFailed(f"{path.name}: Docling failed on pages {pages}: {errors}")
    return _pages(converted.document, pages)


def _subset(path: Path, pages: list[int]) -> io.BytesIO:
    """A PDF of only `pages` (1-based) of `path`, in memory."""
    source = pypdfium2.PdfDocument(path)
    subset = pypdfium2.PdfDocument.new()
    try:
        subset.import_pages(source, [p - 1 for p in pages])
        buffer = io.BytesIO()
        subset.save(buffer)
    finally:
        subset.close()
        source.close()
    buffer.seek(0)
    return buffer


def _pages(document: Any, pages: list[int]) -> dict[int, tuple[list[Table], list[str]]]:
    """Docling's tables and text per original page number."""
    found: dict[int, tuple[list[Table], list[str]]] = {number: ([], []) for number in pages}
    heights = {no: page.size.height for no, page in document.pages.items()}
    for item in document.texts:
        if item.prov:
            found[pages[item.prov[0].page_no - 1]][1].append(item.text)
    for item in document.tables:
        if not item.prov:
            continue
        number = item.prov[0].page_no
        height = heights[number]
        grid = [
            [Cell(c.text, bool(c.column_header), _box(c.bbox, height)) for c in row]
            for row in item.data.grid
        ]
        found[pages[number - 1]][0].append(Table(grid, _box(item.prov[0].bbox, height)))
    return found


def _box(bbox: Any, height: float) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    bottom_left = bbox.coord_origin.name == "BOTTOMLEFT"
    return top_left(bbox.l, bbox.t, bbox.r, bbox.b, bottom_left=bottom_left, height=height)


def _get_converter(result: DoclingResult) -> Any:
    """The process's Docling converter, made on first use (models downloaded first if missing)."""
    global _converter
    if _converter is not None:
        return _converter
    for name, value in _QUIET_ENV.items():
        os.environ.setdefault(name, value)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    folder = models_dir()
    if not models_present():
        result.notices.append(
            f"Docling's models are not in {folder}; downloading them now (about 700 MB, once). "
            f"install.sh does this at install: {MODEL_DOWNLOAD}"
        )
        _download_models(folder)
    options = PdfPipelineOptions(artifacts_path=folder, do_ocr=True, do_table_structure=True)
    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    return _converter


def _download_models(folder: Path) -> None:
    from docling.utils.model_downloader import download_models

    try:
        download_models(
            output_dir=folder,
            with_layout=True,
            with_tableformer=True,
            with_rapidocr=True,
            with_code_formula=False,
            with_picture_classifier=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise DoclingFailed(f"cannot download Docling's models into {folder}: {exc}") from exc
