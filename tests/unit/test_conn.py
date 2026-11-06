"""`fin.db.conn.connect`: read-only by default, explicit write mode, lock retry then E501."""

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from fin.audit.errors import DatabaseLocked, DependencyMissing, ReadOnlyViolation
from fin.db.conn import LockRetry, connect

FAST = LockRetry(attempts=5, base_delay=0.01)

HOLD_LOCK = (
    "import sys, duckdb\n"
    "con = duckdb.connect(sys.argv[1])\n"
    "print('locked', flush=True)\n"
    "sys.stdin.read()\n"
    "con.close()\n"
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.duckdb"
    with connect(path, write=True) as con:
        con.execute("CREATE TABLE t (v DECIMAL(20,2))")
        con.execute("INSERT INTO t VALUES (10.25)")
    return path


@pytest.fixture
def holder(db: Path) -> Iterator[subprocess.Popen[str]]:
    """Another process holding a write connection (and so the file lock) on `db` until stdin
    closes."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stdout.readline() == "locked\n"
    yield proc
    if proc.poll() is None:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_default_connection_reads(db: Path) -> None:
    with connect(db) as con:
        assert con.execute("SELECT v FROM t").fetchall()[0][0] == 10.25


def test_default_connection_rejects_writes_with_e502(db: Path) -> None:
    with pytest.raises(ReadOnlyViolation) as caught, connect(db) as con:
        con.execute("INSERT INTO t VALUES (1)")
    assert caught.value.code == "E502"
    with connect(db) as con:
        assert con.execute("SELECT count(*) FROM t").fetchone() == (1,)


def test_write_mode_writes(db: Path) -> None:
    with connect(db, write=True) as con:
        con.execute("INSERT INTO t VALUES (1)")
    with connect(db) as con:
        assert con.execute("SELECT count(*) FROM t").fetchone() == (2,)


def test_read_only_open_of_missing_file_is_e001(tmp_path: Path) -> None:
    missing = tmp_path / "missing.duckdb"
    with pytest.raises(DependencyMissing) as caught, connect(missing):
        pass
    assert str(caught.value) == f"E001: database {missing} does not exist; run `uv run fin init`"
    assert not missing.exists()


@pytest.mark.parametrize("write", [False, True])
def test_locked_file_retries_then_raises_e501(
    db: Path, holder: subprocess.Popen[str], write: bool
) -> None:
    started = time.monotonic()
    with pytest.raises(DatabaseLocked) as caught, connect(db, write=write, retry=FAST):
        pass
    elapsed = time.monotonic() - started
    assert str(caught.value) == (
        f"E501: {db} is locked by another process; gave up after 5 attempts"
    )
    assert caught.value.exit_code == 2
    # Four backoff sleeps between five attempts: 0.01 + 0.02 + 0.04 + 0.08 seconds.
    assert 0.15 <= elapsed < 10


def test_lock_released_during_backoff_connects(db: Path, holder: subprocess.Popen[str]) -> None:
    assert holder.stdin is not None
    threading.Timer(0.3, holder.stdin.close).start()
    # Backoff sleeps 0.4 + 0.8 + 1.6 + 3.2 seconds: the holder exits long before the last attempt.
    with connect(db, retry=LockRetry(attempts=5, base_delay=0.4)) as con:
        assert con.execute("SELECT v FROM t").fetchall()[0][0] == 10.25
