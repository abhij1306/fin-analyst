"""What a failed document's log event carries besides its message (docs/product.md §8).

`failure_context` is the `context` of the error event and of `review/<company>/<file>.error.json`:
the file's type and size, which reader ran and its version, the PDF add-on chosen (`[pdf] parser`),
the reading given, the statement pages left unread, and what the code that raised the error saw
(`FinError.context`: the page's or sheet's top lines, page and sheet counts). It records what was
read; it changes nothing about how a document is read.
"""

from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fin.audit.errors import DocumentFailure
from fin.config import Config

READERS = {".pdf": "pdfplumber", ".xlsx": "openpyxl", ".xlsm": "openpyxl"}
TOP_LINES = 8  # lines of a page or sheet kept in a context
LINE_WIDTH = 120


def top_lines(texts: Iterable[object], limit: int = TOP_LINES) -> list[str]:
    """The first `limit` texts, each cut to LINE_WIDTH characters."""
    kept: list[str] = []
    for text in texts:
        if len(kept) == limit:
            break
        kept.append(" ".join(str(text).split())[:LINE_WIDTH])
    return kept


def reader_of(path: Path) -> str | None:
    """The library that reads this kind of file, with its version ("pdfplumber 0.11.4"); None for
    a format fin does not read."""
    name = READERS.get(path.suffix.lower())
    if name is None:
        return None
    try:
        return f"{name} {version(name)}"
    except PackageNotFoundError:
        return name


def failure_context(
    path: Path, config: Config, error: DocumentFailure, reading: Path | None
) -> dict[str, object]:
    """The context of one failed document (see the module docstring)."""
    try:
        size: int | None = path.stat().st_size
    except OSError:
        size = None
    context: dict[str, object] = {
        "suffix": path.suffix.lower(),
        "size_bytes": size,
        "reader": reader_of(path),
        "pdf_parser": config.pdf_parser,
        "reading": reading.name if reading else None,
    }
    if error.pages:
        context["unread_pages"] = list(error.pages)
    if error.context:
        context["seen"] = error.context
    return context
