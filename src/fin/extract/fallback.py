"""Ingest's PDF reading: pdfplumber first, then the chosen PDF add-on on what it could not read.

`[pdf] parser` in config.toml names the add-on: "none", "docling", "liteparse" or "llamaparse"
(`fin parser use`). `extract` runs pdfplumber (`fin.extract.pdf`); the statement pages it skipped
(E201-E204), or a scanned PDF (E104; only to an engine whose OCR fin uses, not LiteParse), go to the
chosen engine through `fin.extract.addon`. With no add-on chosen, or the chosen one not installed,
the run logs which pages were not read and the two ways to read them. The add-on's notices and
warnings (E206) are logged as it runs, also when the file then fails. When nothing could be read,
pdfplumber's error is raised again, listing the pages still unread.
"""

from dataclasses import dataclass
from pathlib import Path

from fin.audit.errors import (
    DocumentFailure,
    NoTablesFound,
    ParseError,
    PeriodsNotDetected,
    ScannedPdf,
    UnitNotDetected,
)
from fin.audit.logger import Run
from fin.config import Config
from fin.extract import addon, docling_run, liteparse_run, llamaparse_run
from fin.extract.addon import AddonResult, Engine
from fin.extract.pdf import extract_pdf
from fin.extract.pdf_model import PdfValue, SkippedPage

ENGINES: dict[str, Engine] = {
    docling_run.NAME: docling_run,
    liteparse_run.NAME: liteparse_run,
    llamaparse_run.NAME: llamaparse_run,
}


@dataclass(frozen=True)
class PdfRead:
    by_fin: list[PdfValue]  # pdfplumber's values (notes tables included)
    by_addon: list[PdfValue]
    reader: str  # read_by of by_addon: the engine's name
    unread: list[SkippedPage]  # statement pages neither read
    scanned: bool  # no text layer: the add-on's OCR read it


def engine(name: str) -> Engine | None:
    """The engine `[pdf] parser` names; None for "none"."""
    return ENGINES.get(name)


def cache_dir(root: Path, name: str) -> Path:
    """Where an engine keeps its responses in the data folder (LlamaParse's, by sha256)."""
    return root / "cache" / name


def extract(run: Run, path: Path, name: str, config: Config) -> PdfRead:
    """pdfplumber first; the add-on on what it could not read. When nothing could be read, the
    pdfplumber error is raised again, listing the pages still unread."""
    start = config.fiscal_year_start_month
    reader = config.pdf_parser
    try:
        pdf = extract_pdf(path, start)
    except ScannedPdf as exc:
        found = _read_scanned(run, path, name, config)
        _raise_if_empty(exc, found)
        assert found is not None
        return PdfRead([], found.values, reader, found.unread, scanned=True)
    except (NoTablesFound, PeriodsNotDetected, UnitNotDetected, ParseError) as exc:
        if not exc.pages:
            raise
        found = _read_skipped(run, path, name, config, _skipped_pages(exc.pages))
        _raise_if_empty(exc, found)
        assert found is not None
        return PdfRead([], found.values, reader, found.unread, scanned=False)
    if not pdf.skipped:
        return PdfRead(pdf.values, [], reader, [], scanned=False)
    found = _read_skipped(run, path, name, config, pdf.skipped)
    if found is None:
        return PdfRead(pdf.values, [], reader, pdf.skipped, scanned=False)
    return PdfRead(pdf.values, found.values, reader, found.unread, scanned=False)


def _raise_if_empty(exc: DocumentFailure, found: AddonResult | None) -> None:
    """Raise `exc` again when the add-on was not used or read nothing (with the pages it left)."""
    if found is not None and found.values:
        return
    if found is not None:
        exc.pages = tuple(s.as_dict() for s in found.unread)
    raise exc


_PAGE_ERRORS: dict[str, type[DocumentFailure]] = {
    cls.code: cls for cls in (NoTablesFound, PeriodsNotDetected, UnitNotDetected, ParseError)
}


def _skipped_pages(pages: tuple[dict[str, object], ...]) -> list[SkippedPage]:
    """The unread pages an E2xx error lists, as SkippedPage again."""
    return [
        SkippedPage(
            int(str(p["page"])), str(p["statement"]), _PAGE_ERRORS[str(p["code"])](str(p["reason"]))
        )
        for p in pages
    ]


def _usable(config: Config) -> Engine | None:
    """The chosen engine when it is installed, else None."""
    chosen = engine(config.pdf_parser)
    return chosen if chosen is not None and chosen.installed() else None


def _not_used(run: Run, name: str, config: Config, pages: list[int]) -> None:
    """One notice naming the unread pages, why the add-on did not read them, and the two ways to
    read them."""
    chosen = engine(config.pdf_parser)
    if chosen is None:
        why = 'no PDF add-on is chosen ([pdf] parser = "none" in config.toml)'
        how = "choose one (`fin parser use docling|liteparse|llamaparse`, or /parser)"
    else:
        why = f"the {chosen.LABEL} add-on is not installed"
        how = f"install it (`fin parser use {chosen.NAME}`)"
    run.event(
        "extract",
        f"pages {', '.join(map(str, pages))} not read, and {why}: {how} or give a Claude reading "
        "(`fin ingest --reading`)",
        file=name,
        context={"pages": pages, "pdf_parser": config.pdf_parser, "installed": False},
    )


def _read_skipped(
    run: Run, path: Path, name: str, config: Config, skipped: list[SkippedPage]
) -> AddonResult | None:
    """The add-on's reading of the skipped statement pages, or None when it is not used."""
    chosen = _usable(config)
    if chosen is None:
        _not_used(run, name, config, sorted({s.page for s in skipped}))
        return None
    start = config.fiscal_year_start_month
    found = addon.read_skipped(chosen, path, skipped, start, cache_dir(run.root, chosen.NAME))
    _log(run, name, found)
    return found


def _read_scanned(run: Run, path: Path, name: str, config: Config) -> AddonResult | None:
    """The add-on's OCR reading of a scanned PDF, or None (add-on not used, its OCR not used, or
    too long: E104)."""
    chosen = _usable(config)
    if chosen is None:
        return None
    if not chosen.OCR:
        run.event(
            "extract",
            f"scanned PDF: not read by {chosen.LABEL} (fin does not use its OCR); give a Claude "
            "reading (`fin ingest --reading`)",
            file=name,
        )
        return None
    start = config.fiscal_year_start_month
    found = addon.read_scanned(chosen, path, start, cache_dir(run.root, chosen.NAME))
    if found is None:
        run.event(
            "extract",
            f"scanned PDF longer than {addon.SCANNED_PAGE_LIMIT} pages: not read by {chosen.LABEL}",
            file=name,
        )
        return None
    _log(run, name, found)
    return found


def _log(run: Run, name: str, found: AddonResult) -> None:
    """Log what the add-on did (also when the file then fails): notices, then warnings."""
    for notice in found.notices:
        run.event("extract", notice, file=name)
    for code, message in found.warnings:
        run.event("extract", message, level="warning", code=code, file=name)
