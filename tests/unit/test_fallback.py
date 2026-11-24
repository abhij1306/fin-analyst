"""The dispatcher (fin.extract.fallback): one engine per [pdf] parser name."""

from pathlib import Path

from fin.config import PARSERS
from fin.extract import docling_run, fallback, liteparse_run, llamaparse_run
from fin.extract.addon import ADDON_LABELS


def test_each_parser_name_but_none_has_its_engine() -> None:
    assert fallback.engine("none") is None
    assert fallback.engine("docling") is docling_run
    assert fallback.engine("liteparse") is liteparse_run
    assert fallback.engine("llamaparse") is llamaparse_run
    assert ["none", *fallback.ENGINES] == list(PARSERS)
    assert {name: e.LABEL for name, e in fallback.ENGINES.items()} == ADDON_LABELS


def test_every_engine_has_what_fin_calls() -> None:
    for engine in fallback.ENGINES.values():
        for name in ("installed", "unsupported", "ready", "download_models", "convert"):
            assert callable(getattr(engine, name)), (engine.NAME, name)
        assert engine.MODULE


def test_the_cache_is_in_the_data_folder(tmp_path: Path) -> None:
    assert fallback.cache_dir(tmp_path, "llamaparse") == tmp_path / "cache" / "llamaparse"
