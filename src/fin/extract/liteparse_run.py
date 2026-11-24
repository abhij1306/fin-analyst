"""LiteParse, a PDF add-on engine (`liteparse` extra): local, small and fast (Rust, on CPU).

`fin.extract.addon` hands it the pages pdfplumber skipped (and short scanned PDFs), as one PDF of
only those pages. LiteParse reads the pages' text layer and finds each table with its learned table
model (`tablenet.onnx`, in LiteParse's `ocria-nano` model package): rows of cells with boxes in
top-left 72-DPI points, fin's convention. fin never turns LiteParse's OCR on (`OCR` is False): on a
scanned test report its OCR dropped the brackets of a negative figure, a wrong value fin cannot
catch, so a scanned PDF stays unread (E104) for a Claude reading.

The model package (about 50 MB; its weights come under the ocria-nano model licence, not
LiteParse's Apache-2.0) is pulled when LiteParse is chosen (`fin parser use liteparse`), or at
first use with a notice. The table model gives a header line that wraps (the dates under "As at")
as a row with fewer cells than the table: each of its cells is placed under the columns its box
covers at least half of; a cell that cannot be placed leaves the page unread (E204).
"""

import importlib.util
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fin.audit.errors import DocumentFailure, ParseError, PdfAddonFailed
from fin.extract.addon import Found, PageRead, Request, subset
from fin.extract.pdf_tables import Box, Cell, Grid, Table

NAME = "liteparse"
LABEL = "LiteParse"
MODULE = "liteparse.parser"
MODEL_PACKAGE = "ocria-nano@1.0.0"
MODEL_NAME = "ocria-nano"
TABLE_MODEL = "tablenet.onnx"  # in the model package's folder
MODEL_DOWNLOAD = f"uv run lit models pull {MODEL_PACKAGE}"
OCR = False  # scanned PDFs are not sent to LiteParse (see above)
_TABLES = ("table", "merged_table")  # block kinds holding a table (merged_table: the table model)


def installed() -> bool:
    """True when the LiteParse package can be imported (the add-on is installed)."""
    return importlib.util.find_spec("liteparse") is not None


def unsupported() -> str | None:
    return None


def models_folder() -> Path | None:
    """The installed, complete ocria-nano package's folder, or None."""
    from liteparse import models

    for model in models.list():
        if model.name == MODEL_NAME and model.complete:
            return Path(model.path)
    return None


def ready() -> tuple[str, str | None]:
    folder = models_folder()
    if folder is None:
        return (
            f"its {MODEL_NAME} models (the table model) are not downloaded (ingest downloads them "
            "at first use)",
            f"run `{MODEL_DOWNLOAD}`",
        )
    return f"models in {folder}", None


def download_models() -> None:
    """Pull the ocria-nano model package (LiteParse's own folder). Raises E206."""
    from liteparse import models

    try:
        models.pull(MODEL_PACKAGE, quiet=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PdfAddonFailed(f"cannot download LiteParse's {MODEL_NAME} models: {exc}") from exc


def convert(request: Request, notices: list[str]) -> Found:
    """Run LiteParse once on `request.pages`: tables and text lines per page. Raises E206."""
    folder = models_folder()
    if folder is None:
        notices.append(
            f"LiteParse's {MODEL_NAME} models are not downloaded; downloading them now (about "
            f"50 MB, once). `fin parser use liteparse` does this when LiteParse is chosen: "
            f"{MODEL_DOWNLOAD}"
        )
        download_models()
        folder = models_folder()
        if folder is None:
            raise PdfAddonFailed(f"LiteParse's {MODEL_NAME} models are still missing after a pull")
    from liteparse import LiteParse
    from liteparse import ParseError as LiteParseError

    name, pages = request.path.name, request.pages
    try:
        parser = LiteParse(
            ocr_enabled=False,
            extract_blocks=True,
            table_model=folder / TABLE_MODEL,
            quiet=True,
        )
        result = parser.parse(subset(request.path, pages).getvalue())
    except (LiteParseError, OSError, RuntimeError, ValueError) as exc:
        raise PdfAddonFailed(f"{name}: LiteParse failed on pages {pages}: {exc}") from exc
    return read_pages(result.pages, pages)


def read_pages(parsed: Iterable[Any], pages: list[int]) -> Found:
    """LiteParse's parsed pages (page i is `pages[i - 1]`) as tables and text lines per original
    page. Each page's layout blocks: a table block's header and rows become a grid; every other
    block's text is a text line."""
    found: Found = {number: PageRead() for number in pages}
    for page in parsed:
        number = pages[page.page_num - 1]
        read = found[number]
        for block in page.blocks or []:
            if block.kind in _TABLES and block.rows:
                try:
                    read.tables.append(_table(block))
                except DocumentFailure as exc:
                    read.error = type(exc)(f"page {number}: {exc.message}")
            elif block.text:
                read.texts.append(block.text)
    return found


def _table(block: Any) -> Table:
    header = [[Cell(c.text, True, _box(c.bbox)) for c in block.header]] if block.header else []
    rows = [[Cell(c.text, False, _box(c.bbox)) for c in row] for row in block.rows]
    return Table(square(header + rows), _box(block.bbox))


def _box(rect: Any) -> Box | None:
    if rect is None:
        return None
    return (rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)


def square(grid: Grid) -> Grid:
    """The grid with every row as wide as the table. A shorter row's cells go under the columns
    whose span (from the full rows' cells) they cover at least half of. Raises E204 when a cell has
    no box, covers half of no column, or two cells land in one column."""
    width = max((len(row) for row in grid), default=0)
    if all(len(row) == width for row in grid):
        return grid
    spans: list[tuple[float, float] | None] = [None] * width
    for row in grid:
        if len(row) != width:
            continue
        for i, cell in enumerate(row):
            if cell.bbox is not None and cell.text.strip():
                left, right = cell.bbox[0], cell.bbox[2]
                old = spans[i]
                spans[i] = (min(left, old[0]), max(right, old[1])) if old else (left, right)
    return [row if len(row) == width else _place(row, spans) for row in grid]


def _place(row: list[Cell], spans: list[tuple[float, float] | None]) -> list[Cell]:
    placed = [Cell("") for _ in spans]
    for cell in row:
        if not cell.text.strip():
            continue
        if cell.bbox is None:
            raise ParseError(f"table cell {cell.text[:30]!r} has no position ({LABEL})")
        overlaps = [_overlap(cell.bbox, span) for span in spans]
        columns = [
            i
            for i, span in enumerate(spans)
            if span is not None and span[1] > span[0] and overlaps[i] >= (span[1] - span[0]) / 2
        ]
        if not columns:
            raise ParseError(f"table cell {cell.text[:30]!r} is under no column ({LABEL})")
        for i in columns:
            if placed[i].text:
                raise ParseError(
                    f"table cells {placed[i].text[:30]!r} and {cell.text[:30]!r} fall in one "
                    f"column ({LABEL})"
                )
            placed[i] = cell
    return placed


def _overlap(box: Box, span: tuple[float, float] | None) -> float:
    if span is None:
        return 0.0
    return max(0.0, min(box[2], span[1]) - max(box[0], span[0]))
