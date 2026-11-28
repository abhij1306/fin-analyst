"""LlamaParse, a PDF add-on engine (`llamaparse` extra, the `llama-cloud` SDK): LlamaIndex's cloud
parser.

`fin.extract.addon` hands it the pages pdfplumber skipped (and short scanned PDFs). One PDF of only
those pages is uploaded to LlamaParse's cloud (tier TIER), under a neutral file name: those pages
leave this Mac, which the user chose with `fin parser use llamaparse`. The API key comes only from
the environment variable LLAMA_CLOUD_API_KEY: fin never reads it from or writes it to the repo,
config.toml or the data folder, and never logs it. Without it the pages stay unread (E206, naming
the variable).

The response is saved in the data folder (`cache/llamaparse/<sha256>-p<pages>.json`, keyed by the
file's sha256 and the pages sent), so ingesting the file again does not call the API (or pay)
again. Tables are read from the response's structured items (each table's rows of cells), not from
its markdown. LlamaParse gives boxes per table, not per cell, in points from the page's top-left
corner, so every figure points at its table's box.
"""

# The engine is an optional extra, not installed for the type check (ci.yml typecheck job).
# pyright: reportMissingImports=false

import hashlib
import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import PdfAddonFailed
from fin.extract.addon import Found, PageRead, Request, subset
from fin.extract.pdf_tables import Box, Cell, Table

NAME: str = "llamaparse"
LABEL: str = "LlamaParse"
MODULE: str = "llama_cloud"
OCR: bool = True  # short scanned PDFs are uploaded and read too
API_KEY_ENV = "LLAMA_CLOUD_API_KEY"
TIER = "agentic"  # LlamaParse's tier for complex tables
VERSION = "latest"
UPLOAD_NAME = "pages.pdf"  # the file name sent: never the document's own


def installed() -> bool:
    """True when the llama-cloud SDK can be imported (the add-on is installed)."""
    return importlib.util.find_spec("llama_cloud") is not None


def unsupported() -> str | None:
    return None


def key_set() -> bool:
    return bool(os.environ.get(API_KEY_ENV))


def ready() -> tuple[str, str | None]:
    if not key_set():
        return (
            f"{API_KEY_ENV} is not set: pages pdfplumber skips stay unread",
            f"set {API_KEY_ENV} in your shell (for example in ~/.zshrc), not in config.toml",
        )
    return f"{API_KEY_ENV} is set; skipped pages are uploaded to LlamaParse's cloud", None


def download_models() -> None:
    """LlamaParse runs in the cloud: nothing to download."""


def cache_file(request: Request) -> Path:
    digest = hashlib.sha256(request.path.read_bytes()).hexdigest()
    pages = "-".join(map(str, request.pages))
    return request.cache_dir / f"{digest}-p{pages}.json"


def convert(request: Request, notices: list[str]) -> Found:
    """LlamaParse's reading of `request.pages`: the saved response when there is one, else one API
    call (saved). Raises E206 when the key is not set or the call fails."""
    name, pages = request.path.name, request.pages
    saved = cache_file(request)
    if saved.is_file():
        try:
            response = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PdfAddonFailed(
                f"{name}: LlamaParse's saved response {saved} cannot be read ({exc}); delete it "
                "to ask LlamaParse again"
            ) from exc
        notices.append(f"LlamaParse's saved response for pages {pages} used ({saved.name})")
        return _read(response, request)
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise PdfAddonFailed(
            f"{name}: LlamaParse needs an API key in the environment variable {API_KEY_ENV}, "
            f"which is not set; pages {pages} were not sent"
        )
    response = _parse(request, key)
    try:
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(response), encoding="utf-8")
    except OSError as exc:
        notices.append(f"pages {pages} uploaded to LlamaParse; its response not saved ({exc})")
    else:
        notices.append(f"pages {pages} uploaded to LlamaParse; its response saved as {saved.name}")
    return _read(response, request)


def _read(response: dict[str, Any], request: Request) -> Found:
    """`read_items`; a response not in the shape fin expects is E206 (the pages stay unread)."""
    try:
        return read_items(response, request.pages)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PdfAddonFailed(
            f"{request.path.name}: LlamaParse's response for pages {request.pages} is not in the "
            f"shape fin reads ({type(exc).__name__}: {exc})"
        ) from exc


def _parse(request: Request, key: str) -> dict[str, Any]:
    from llama_cloud import LlamaCloud, LlamaCloudError, PollingError, PollingTimeoutError

    name, pages = request.path.name, request.pages
    try:
        result = LlamaCloud(api_key=key).parsing.parse(
            tier=TIER,
            version=VERSION,
            upload_file=(UPLOAD_NAME, subset(request.path, pages).getvalue(), "application/pdf"),
            expand=["items"],
            output_options={"markdown": {"tables": {"merge_continued_tables": False}}},
        )
    except (LlamaCloudError, PollingError, PollingTimeoutError, OSError) as exc:
        raise PdfAddonFailed(f"{name}: LlamaParse failed on pages {pages}: {exc}") from exc
    if result.job.status != "COMPLETED":
        problem = result.job.error_message or result.job.status
        raise PdfAddonFailed(f"{name}: LlamaParse failed on pages {pages}: {problem}")
    return result.model_dump(mode="json")


def read_items(response: dict[str, Any], pages: list[int]) -> Found:
    """The response's structured items (page i is `pages[i - 1]`) as tables and text lines per
    original page. A page LlamaParse could not parse is unread with its error (E206)."""
    found: Found = {number: PageRead() for number in pages}
    for page in (response.get("items") or {}).get("pages", []):
        index = page["page_number"] - 1
        if not 0 <= index < len(pages):
            raise PdfAddonFailed(f"LlamaParse's response has a page {index + 1} it was not sent")
        read = found[pages[index]]
        if not page.get("success", False):
            read.error = PdfAddonFailed(
                f"page {pages[index]}: LlamaParse could not parse it: {page.get('error')}"
            )
            continue
        for item in page.get("items", []):
            if item.get("type") == "table" or "rows" in item:
                read.tables.append(_table(item))
            elif isinstance(item.get("value"), str) and item["value"].strip():
                read.texts.append(item["value"])
    return found


def _table(item: dict[str, Any]) -> Table:
    grid = [[Cell(_text(value)) for value in row] for row in item.get("rows", [])]
    return Table(grid, _box(item.get("bbox") or []))


def _text(value: str | float | None) -> str:
    """A cell as text: a number LlamaParse gave as a number is written out (no exponent; a whole
    number without ".0", so a year stays a year)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if float(value).is_integer():
        return str(int(value))
    return format(Decimal(repr(value)), "f")


def _box(boxes: list[dict[str, float]]) -> Box | None:
    """The union of a table's boxes (x, y, w, h from the page's top-left), or None."""
    if not boxes:
        return None
    return (
        min(b["x"] for b in boxes),
        min(b["y"] for b in boxes),
        max(b["x"] + b["w"] for b in boxes),
        max(b["y"] + b["h"] for b in boxes),
    )
