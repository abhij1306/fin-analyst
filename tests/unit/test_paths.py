from pathlib import Path

import pytest

from fin.paths import data_dir


def test_fin_data_dir_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIN_DATA_DIR", str(tmp_path / "custom"))
    assert data_dir() == (tmp_path / "custom").resolve()


def test_default_is_finanalysis_in_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIN_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert data_dir() == (tmp_path / "FinAnalysis").resolve()
