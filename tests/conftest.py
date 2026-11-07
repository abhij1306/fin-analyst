import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fin.db.conn import connect
from fin.paths import audit_db_path

SAMPLES = Path(__file__).parent / "fixtures" / "sample-financials"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tests marked `docling` run the real Docling: skipped where the add-on is not installed (the
    default install; CI's docling job has it)."""
    if importlib.util.find_spec("docling") is not None:
        return
    skip = pytest.mark.skip(reason="the Docling add-on is not installed (uv sync --extra docling)")
    for item in items:
        if item.get_closest_marker("docling") is not None:
            item.add_marker(skip)


@pytest.fixture
def samples() -> Path:
    """The synthetic documents (tests/fixtures/sample-financials; see its README)."""
    return SAMPLES


@pytest.fixture
def answer_key() -> dict[str, Any]:
    """Expected values, in rupees, for every line of the synthetic documents."""
    return json.loads((SAMPLES / "answer_key.json").read_text(encoding="utf-8"))


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point FIN_DATA_DIR at an empty temp folder (not yet created)."""
    root = tmp_path / "FinAnalysis"
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    return root.resolve()


@pytest.fixture
def audit_query(data_root: Path) -> Callable[..., list[tuple[Any, ...]]]:
    """Run a read-only query against the audit database."""

    def query(sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        with connect(audit_db_path(data_root)) as con:
            return con.execute(sql, params or []).fetchall()

    return query
