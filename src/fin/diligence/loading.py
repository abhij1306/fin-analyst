"""What every diligence loader (`fin.diligence.sources`, `.bank`; registers and payroll through
`sources`) shares: refusing a file already loaded, archiving the original, the `source_files` row,
multi-row inserts and the closing log line. One place, so the loaders stay alike."""

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DataFolderNotWritable, DuplicateFile
from fin.audit.logger import Run
from fin.db.conn import utcnow
from fin.paths import archive_dir

CHUNK = 500  # rows per INSERT


@dataclass(frozen=True)
class Loaded:
    file_id: int
    period: str
    lines: list[str]  # what to show, first line the summary


def clean_text(value: Any) -> str | None:
    """A cell as text with spaces collapsed (a whole-number float as digits); None when blank."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split()) or None


def insert_rows(
    con: duckdb.DuckDBPyConnection, table: str, columns: str, rows: list[list[Any]]
) -> None:
    """Insert rows with multi-row INSERTs of CHUNK rows."""
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        marks = ", ".join("(" + ", ".join("?" for _ in chunk[0]) + ")" for _ in chunk)
        con.execute(
            f"INSERT INTO {table} ({columns}) VALUES {marks}", [v for row in chunk for v in row]
        )


def archive_copy(run: Run, company: str, path: Path, sha256: str) -> str:
    target = archive_dir(run.root, company) / f"{sha256}{path.suffix.lower()}"
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot copy {path} to {target}: {exc}") from exc
    return target.relative_to(run.root).as_posix()


def refuse_duplicate(con: duckdb.DuckDBPyConnection, name: str, sha256: str) -> None:
    """E105 when a file with this sha256 is already a source file (even a superseded one)."""
    earlier = con.execute(
        "SELECT file_id FROM source_files WHERE sha256 = ? ORDER BY file_id DESC", [sha256]
    ).fetchone()
    if earlier is not None:
        raise DuplicateFile(f"{name}: already loaded as source file_id {earlier[0]} (same sha256)")


def add_file(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    *,
    kind: str,
    name: str,
    sha256: str,
    archive: str,
    period: str,
    start: date,
    end: date,
    basis: str,
    profile: str,
) -> dict[str, Any]:
    """Insert the `source_files` row of a file being loaded; the row (with its file_id)."""
    file: dict[str, Any] = {
        "engagement_id": engagement["engagement_id"],
        "kind": kind,
        "file_name": name,
        "sha256": sha256,
        "archive_path": archive,
        "period": period,
        "period_start": start,
        "period_end": end,
        "basis": basis,
        "profile": profile,
        "status": "loaded",
        "run_id": run.run_id,
        "ingested_at": utcnow(),
    }
    row = con.execute(
        f"INSERT INTO source_files ({', '.join(file)}) VALUES ({', '.join('?' for _ in file)}) "
        "RETURNING file_id",
        list(file.values()),
    ).fetchone()
    assert row is not None
    file["file_id"] = int(row[0])
    return file


def log_load(
    run: Run, company: str, file: dict[str, Any], lines: list[str], archive: str
) -> Loaded:
    """Count the load and log it (`lines`: what is shown, the head first); the `Loaded`."""
    run.loaded += 1
    run.event(
        "ingest",
        "; ".join(lines),
        file=f"{company}/{file['file_name']}",
        context={
            "file_id": file["file_id"],
            "kind": file["kind"],
            "period": file["period"],
            "archive": archive,
        },
    )
    return Loaded(file["file_id"], file["period"], lines)
