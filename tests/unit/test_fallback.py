"""The dispatcher (fin.extract.fallback): one engine per [pdf] parser name."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fin.audit.errors import ScannedPdf
from fin.config import PARSERS
from fin.extract import docling_run, fallback, liteparse_run, llamaparse_run
from fin.extract.addon import ADDON_LABELS, Request


def test_each_parser_name_but_none_has_its_engine() -> None:
    assert fallback.engine("none") is None
    assert fallback.engine("docling") is docling_run
    assert fallback.engine("liteparse") is liteparse_run
    assert fallback.engine("llamaparse") is llamaparse_run
    assert list(PARSERS) == ["none", *fallback.ENGINES]
    assert {name: e.LABEL for name, e in fallback.ENGINES.items()} == ADDON_LABELS


def test_every_engine_has_what_fin_calls() -> None:
    for engine in fallback.ENGINES.values():
        for name in ("installed", "unsupported", "ready", "download_models", "convert"):
            assert callable(getattr(engine, name)), (engine.NAME, name)
        assert engine.MODULE


def test_the_cache_is_in_the_data_folder(tmp_path: Path) -> None:
    assert fallback.cache_dir(tmp_path, "llamaparse") == tmp_path / "cache" / "llamaparse"


def test_a_scanned_pdf_is_not_sent_to_an_engine_whose_ocr_fin_does_not_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LiteParse's OCR dropped a minus sign on a scanned test report: a scanned PDF stays E104 (for
    a Claude reading) when LiteParse is chosen, and LiteParse is never asked."""

    def scanned(path: Path, start: int) -> Any:
        raise ScannedPdf("scan.pdf: no text layer")

    def convert(request: Request, notices: list[str]) -> Any:
        pytest.fail("LiteParse was asked to OCR")

    monkeypatch.setattr(fallback, "extract_pdf", scanned)
    monkeypatch.setattr(liteparse_run, "installed", lambda: True)
    monkeypatch.setattr(liteparse_run, "convert", convert)
    events: list[str] = []
    run = SimpleNamespace(root=tmp_path, event=lambda kind, message, **_: events.append(message))
    config = SimpleNamespace(pdf_parser="liteparse", fiscal_year_start_month=4)

    with pytest.raises(ScannedPdf):
        fallback.extract(run, tmp_path / "scan.pdf", "acme/scan.pdf", config)  # type: ignore[arg-type]

    assert events == [
        "scanned PDF: not read by LiteParse (fin does not use its OCR); give a Claude reading "
        "(`fin ingest --reading`)"
    ]
    assert [e.NAME for e in fallback.ENGINES.values() if e.OCR] == ["docling", "llamaparse"]
