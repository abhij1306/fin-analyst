import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import fin.db.migrate
from fin.db.conn import connect
from fin.paths import audit_db_path


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point FIN_DATA_DIR at an empty temp folder (not yet created)."""
    root = tmp_path / "FinAnalysis"
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    return root.resolve()


@pytest.fixture
def migrations_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the real migrations folder that fin uses instead of the real one."""
    target = tmp_path / "migrations"
    shutil.copytree(fin.db.migrate.MIGRATIONS_DIR, target)
    monkeypatch.setattr("fin.db.migrate.MIGRATIONS_DIR", target)
    return target


@pytest.fixture
def audit_query(data_root: Path) -> Callable[..., list[tuple[Any, ...]]]:
    """Run a read-only query against the audit database."""

    def query(sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        with connect(audit_db_path(data_root)) as con:
            return con.execute(sql, params or []).fetchall()

    return query
