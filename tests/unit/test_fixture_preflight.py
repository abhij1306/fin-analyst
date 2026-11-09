"""The fixture generator stops with a message when its fonts or recalculation script are missing."""

import sys
from pathlib import Path

import pytest

GENERATOR = Path(__file__).resolve().parents[1] / "fixtures" / "sample-financials" / "generator"
sys.path.insert(0, str(GENERATOR))
import preflight  # noqa: E402


def test_fonts_found_in_the_configured_folder(tmp_path, monkeypatch):
    for name in preflight.FONT_FILES:
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setenv("FIN_FIXTURE_FONT_DIR", str(tmp_path))
    assert preflight.find_fonts() == (tmp_path / "DejaVuSans.ttf", tmp_path / "DejaVuSans-Bold.ttf")


def test_missing_fonts_exit_with_a_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_FIXTURE_FONT_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="FIN_FIXTURE_FONT_DIR"):
        preflight.find_fonts()


def test_recalc_script_from_env(tmp_path, monkeypatch):
    script = tmp_path / "recalc.py"
    script.write_text("")
    monkeypatch.setenv("FIN_FIXTURE_RECALC_SCRIPT", str(script))
    assert preflight.find_recalc() == script


def test_missing_recalc_script_exits_with_a_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_FIXTURE_RECALC_SCRIPT", str(tmp_path / "nope.py"))
    with pytest.raises(SystemExit, match="Recalculation script not found"):
        preflight.find_recalc()
