"""Docling, a PDF add-on engine (`docling` extra): local, layout and table-structure models on CPU.

`fin.extract.addon` hands it the pages pdfplumber skipped (and short scanned PDFs). One PDF of only
those pages goes through Docling once: layout, table structure and OCR (Docling's defaults). The
converter is made once per process and reused, so a batch ingest loads the models once. Its tables
become `pdf_tables.Table` grids (Docling's column-header flags, boxes turned to fin's top-left
convention).

Docling downloads its models (about 700 MB) into `models_dir()` when it is chosen
(`fin parser use docling`: `docling-tools models download layout tableformer rapidocr`); if they
are missing at first use, `convert` downloads them there and says so in a notice. Not on Intel
Macs: PyTorch has no macOS x86_64 build.
"""

import importlib.util
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from fin.audit.errors import PdfAddonFailed
from fin.extract.addon import Found, PageRead, Request, subset
from fin.extract.pdf_tables import Cell, Table, top_left

NAME = "docling"
LABEL = "Docling"
MODULE = "docling.document_converter"
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
_converter: Any = None  # made on first use, reused for every document of the process


def installed() -> bool:
    """True when the Docling package can be imported (the add-on is installed)."""
    return importlib.util.find_spec("docling") is not None


def unsupported() -> str | None:
    if sys.platform == "darwin" and platform.machine() == "x86_64":
        return "Docling needs an Apple silicon Mac (PyTorch has no Intel macOS build)"
    return None


def models_dir() -> Path:
    """Where `docling-tools models download` puts the models (DOCLING_CACHE_DIR/models, by
    default ~/.cache/docling/models)."""
    cache = os.environ.get("DOCLING_CACHE_DIR")
    return (Path(cache) if cache else Path.home() / ".cache" / "docling") / "models"


def models_present() -> bool:
    folder = models_dir()
    return all((folder / name).is_dir() for name in MODEL_FOLDERS)


def ready() -> tuple[str, str | None]:
    if not models_present():
        return (
            f"its models are not in {models_dir()} (ingest downloads them at first use)",
            f"run `{MODEL_DOWNLOAD}`",
        )
    return f"models in {models_dir()}", None


def download_models() -> None:
    """Download Docling's models into `models_dir()`. Raises E206."""
    from docling.utils.model_downloader import download_models as download

    folder = models_dir()
    try:
        download(
            output_dir=folder,
            with_layout=True,
            with_tableformer=True,
            with_rapidocr=True,
            with_code_formula=False,
            with_picture_classifier=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PdfAddonFailed(f"cannot download Docling's models into {folder}: {exc}") from exc


def convert(request: Request, notices: list[str]) -> Found:
    """Run Docling once on `request.pages`: tables and text lines per page. Raises E206."""
    converter = _get_converter(notices)
    name, pages = request.path.name, request.pages
    from docling.datamodel.base_models import ConversionStatus, DocumentStream
    from docling.exceptions import ConversionError

    try:
        converted = converter.convert(
            DocumentStream(name=name, stream=subset(request.path, pages)), raises_on_error=False
        )
    except (ConversionError, RuntimeError, ValueError, OSError, MemoryError) as exc:
        raise PdfAddonFailed(f"{name}: Docling failed on pages {pages}: {exc}") from exc
    if converted.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = "; ".join(e.error_message for e in converted.errors) or str(converted.status)
        raise PdfAddonFailed(f"{name}: Docling failed on pages {pages}: {errors}")
    return read_document(converted.document, pages)


def read_document(document: Any, pages: list[int]) -> Found:
    """Docling's tables and text per original page number (page i of `document` is
    `pages[i - 1]`)."""
    found: Found = {number: PageRead() for number in pages}
    heights = {no: page.size.height for no, page in document.pages.items()}
    for item in document.texts:
        if item.prov:
            found[pages[item.prov[0].page_no - 1]].texts.append(item.text)
    for item in document.tables:
        if not item.prov:
            continue
        number = item.prov[0].page_no
        height = heights[number]
        grid = [
            [Cell(c.text, bool(c.column_header), _box(c.bbox, height)) for c in row]
            for row in item.data.grid
        ]
        found[pages[number - 1]].tables.append(Table(grid, _box(item.prov[0].bbox, height)))
    return found


def _box(bbox: Any, height: float) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    bottom_left = bbox.coord_origin.name == "BOTTOMLEFT"
    return top_left(bbox.l, bbox.t, bbox.r, bbox.b, bottom_left=bottom_left, height=height)


def _get_converter(notices: list[str]) -> Any:
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
        notices.append(
            f"Docling's models are not in {folder}; downloading them now (about 700 MB, once). "
            f"`fin parser use docling` does this when Docling is chosen: {MODEL_DOWNLOAD}"
        )
        download_models()
    options = PdfPipelineOptions(artifacts_path=folder, do_ocr=True, do_table_structure=True)
    _converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    return _converter
