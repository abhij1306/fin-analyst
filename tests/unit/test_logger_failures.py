"""A run whose JSONL log cannot be written ends cleanly: the error is reported once, and the end row
in the audit database records the exit code the command returns."""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer

from fin.audit.errors import SqlFailed
from fin.audit.logger import Run, execute
from fin.db.schema import ensure_database
from fin.paths import audit_db_path

Query = Callable[..., list[tuple[Any, ...]]]


def break_logs(run: Run) -> None:
    """Replace this run's log folder with a file, so appending to its JSONL fails."""
    folder = run.jsonl_path.parent
    shutil.rmtree(folder)
    folder.write_text("not a folder", encoding="utf-8")


def end_rows(audit_query: Query) -> list[tuple[str, str, int]]:
    return audit_query("SELECT command, status, exit_code FROM runs WHERE record = 'end'")


@pytest.fixture
def root(data_root: Path) -> Path:
    (data_root / "logs").mkdir(parents=True)
    ensure_database(audit_db_path(data_root), "audit")
    return data_root


def test_log_failure_after_the_body_is_recorded_as_the_exit_code(
    root: Path, audit_query: Query, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(typer.Exit) as e:
        execute("probe", {}, break_logs)

    assert e.value.exit_code == 2
    assert end_rows(audit_query) == [("probe", "error", 2)]
    assert "not written to logs/: E003: cannot write" in capsys.readouterr().err


def test_command_error_logged_while_the_log_is_broken_does_not_fail_again(
    root: Path, audit_query: Query, capsys: pytest.CaptureFixture[str]
) -> None:
    def body(run: Run) -> None:
        break_logs(run)
        raise SqlFailed("boom")

    with pytest.raises(typer.Exit) as e:
        execute("probe", {}, body)

    assert e.value.exit_code == 2
    assert end_rows(audit_query) == [("probe", "error", 2)]
    assert audit_query("SELECT error_code, message FROM events") == [("E504", "boom")]
    assert capsys.readouterr().err.startswith("E504: boom\n")
