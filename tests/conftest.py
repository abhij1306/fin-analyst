from pathlib import Path

import pytest


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point FIN_DATA_DIR at an empty temp folder (not yet created)."""
    root = tmp_path / "FinAnalysis"
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    return root
