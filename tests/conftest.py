import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import audit_db_path

SAMPLES = Path(__file__).parent / "fixtures" / "sample-financials"
_runner = CliRunner()


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


@pytest.fixture(scope="session")
def clean_template(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Path]:
    """`clean_template("kestrel", ...)`: a data folder with those clean synthetic companies ingested
    (each whole folder under clean/, in the order given). Each set is ingested once per test process
    (once per xdist worker) and shared, so never change it: copy it (`shutil.copytree`) and point
    FIN_DATA_DIR at the copy. Copying takes milliseconds; an ingest takes seconds."""
    made: dict[tuple[str, ...], Path] = {}

    def template(*companies: str) -> Path:
        if companies not in made:
            root = (tmp_path_factory.mktemp("clean") / "FinAnalysis").resolve()
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("FIN_DATA_DIR", str(root))
                assert _runner.invoke(app, ["init"]).exit_code == 0
                for company in companies:
                    result = _runner.invoke(
                        app, ["ingest", company, str(SAMPLES / "clean" / company)]
                    )
                    assert result.exit_code == 0, result.output
            made[companies] = root
        return made[companies]

    return template


@pytest.fixture
def clean_root(data_root: Path, clean_template: Callable[..., Path]) -> Callable[..., Path]:
    """`clean_root("kestrel", ...)`: this test's own copy of `clean_template(...)`, at the data
    folder FIN_DATA_DIR points at."""

    def copy(*companies: str) -> Path:
        shutil.copytree(clean_template(*companies), data_root)
        return data_root

    return copy


@pytest.fixture
def audit_query(data_root: Path) -> Callable[..., list[tuple[Any, ...]]]:
    """Run a read-only query against the audit database."""

    def query(sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        with connect(audit_db_path(data_root)) as con:
            return con.execute(sql, params or []).fetchall()

    return query
