"""The only way to open a DuckDB file (CLAUDE.md).

Connections are short-lived (a `with` block) and read-only unless the caller asks for `write=True`.
Opening a file another process holds is retried with exponential backoff; after the last attempt it
raises E501.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb

from fin.audit.errors import (
    DatabaseLocked,
    DataFolderNotWritable,
    DependencyMissing,
    ReadOnlyViolation,
)

# DuckDB's message when another process holds the file: "Could not set lock on file ... Conflicting
# lock is held" on macOS and Linux, "being used by another process" on Windows.
_LOCK_MARKERS = ("could not set lock", "conflicting lock", "being used by another process")


@dataclass(frozen=True)
class LockRetry:
    """How to retry a locked file: `attempts` tries in total, sleeping `base_delay` seconds after
    the first failure and doubling each time. Five tries is PLAN.md §9; tests shorten
    `base_delay`."""

    attempts: int = 5
    base_delay: float = 0.2


DEFAULT_RETRY = LockRetry()


@contextmanager
def connect(
    path: Path, *, write: bool = False, retry: LockRetry = DEFAULT_RETRY
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open `path` for one short unit of work. Read-only unless `write=True`; a write mode open
    creates the file.

    Raises E001 if a read-only open finds no file, E501 if the file stays locked, E502 if a
    statement tries to write through a read-only connection, and E003 if the file cannot be opened
    for any other reason.
    """
    if not write and not path.is_file():
        raise DependencyMissing(f"database {path} does not exist; run `uv run fin init`")
    con = _open(path, write, retry)
    try:
        yield con
    except duckdb.InvalidInputException as exc:
        if not write and "read-only mode" in str(exc):
            raise ReadOnlyViolation(f"write attempted on read-only connection to {path}") from exc
        raise
    finally:
        con.close()


def _is_lock(exc: duckdb.IOException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _LOCK_MARKERS)


def _open(path: Path, write: bool, retry: LockRetry) -> duckdb.DuckDBPyConnection:
    last: duckdb.IOException | None = None
    for attempt in range(1, retry.attempts + 1):
        try:
            return duckdb.connect(str(path), read_only=not write)
        except duckdb.IOException as exc:
            if not _is_lock(exc):
                cause = " ".join(str(exc).split())
                raise DataFolderNotWritable(f"cannot open database {path}: {cause}") from exc
            last = exc
        if attempt < retry.attempts:
            time.sleep(retry.base_delay * 2 ** (attempt - 1))
    raise DatabaseLocked(
        f"{path} is locked by another process; gave up after {retry.attempts} attempts"
    ) from last
