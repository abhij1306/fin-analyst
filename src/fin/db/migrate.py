"""Numbered SQL migrations for every database file (PLAN.md §10).

Migrations live in `migrations/<kind>/NNN_name.sql`, numbered 001, 002, ... with no gaps. A kind is
one database shape: `audit` for `shared/audit.duckdb`, `dictionary` for
`shared/dictionary.duckdb`, `company` for each `companies/<company>.duckdb`. A file's schema
version is the highest version in its `schema_version` table (0 when the file or the table does
not exist).

`migrate_all`:
1. Checks every file first. A file newer than the code stops everything with E004 before anything
   changes.
2. Copies each file that has pending migrations to `backups/<run_id>/<path in data folder>`.
3. Applies each pending migration in its own transaction and appends a `schema_version` row with it.
4. On any failure, restores the file from its backup (or removes a file that did not exist before),
   records the failure in the audit `migrations` table and raises E503.
"""

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from fin.audit.errors import (
    DataFolderNotWritable,
    DependencyVersionMismatch,
    MigrationFailed,
    SchemaNewerThanCode,
)
from fin.db.conn import DEFAULT_RETRY, LockRetry, connect
from fin.paths import audit_db_path, backups_dir, companies_dir, dictionary_db_path

if TYPE_CHECKING:
    from fin.audit.logger import Run

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_FILE_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    applied_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class Target:
    path: Path
    kind: str


@dataclass(frozen=True)
class MigrationResult:
    db_file: str
    from_version: int
    to_version: int
    backup: str | None


def load_migrations(kind: str) -> list[Migration]:
    """Read `MIGRATIONS_DIR/kind/*.sql` in order. Raises E503 if the folder or the numbering is
    wrong."""
    folder = MIGRATIONS_DIR / kind
    if not folder.is_dir():
        raise MigrationFailed(f"no migrations folder for database kind {kind!r}: {folder}")
    files = sorted(path for path in folder.iterdir() if path.suffix == ".sql")
    migrations: list[Migration] = []
    for expected, path in enumerate(files, start=1):
        match = _FILE_RE.match(path.name)
        if match is None or int(match.group(1)) != expected:
            raise MigrationFailed(
                f"{path.name}: migrations in {folder} must be named NNN_name.sql and numbered "
                f"001, 002, ... with no gaps (expected {expected:03d})"
            )
        migrations.append(Migration(expected, path.stem, path.read_text(encoding="utf-8")))
    return migrations


def latest_version(kind: str) -> int:
    return len(load_migrations(kind))


def schema_version(con: duckdb.DuckDBPyConnection) -> int:
    """The schema version of an open database: max(schema_version.version), or 0 without that
    table."""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = 'schema_version'"
    ).fetchone()
    if row is None or row[0] == 0:
        return 0
    version = con.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()
    return 0 if version is None else int(version[0])


def current_version(path: Path, retry: LockRetry = DEFAULT_RETRY) -> int:
    """The schema version of the file at `path`; 0 if the file does not exist."""
    if not path.is_file():
        return 0
    with connect(path, retry=retry) as con:
        return schema_version(con)


def targets(root: Path) -> list[Target]:
    """Every database file `fin migrate` manages: the shared audit and dictionary databases (created
    if missing) and each existing company database."""
    found = [Target(audit_db_path(root), "audit"), Target(dictionary_db_path(root), "dictionary")]
    found.extend(Target(path, "company") for path in sorted(companies_dir(root).glob("*.duckdb")))
    return found


def migrate_all(
    root: Path,
    run: "Run",
) -> list[MigrationResult]:
    """Bring every target to the latest schema version. Idempotent: files already current are left
    untouched."""
    if not (root / "shared").is_dir():
        raise DataFolderNotWritable(f"{root} is not set up; run `uv run fin init`")
    plans: list[tuple[Target, int, list[Migration]]] = []
    for target in targets(root):
        migrations = load_migrations(target.kind)
        current = current_version(target.path)
        if current > len(migrations):
            raise SchemaNewerThanCode(
                f"{_rel(root, target.path)} is at schema version {current}, but this fin knows "
                f"only up to {len(migrations)}; update fin (`git pull`, then `uv sync --frozen`)"
            )
        plans.append((target, current, migrations[current:]))

    results: list[MigrationResult] = []
    for target, current, pending in plans:
        if not pending:
            results.append(MigrationResult(_rel(root, target.path), current, current, None))
            continue
        results.append(_migrate_file(root, target.path, current, pending, run))
    return results


def ensure_current(root: Path, path: Path, kind: str, run: "Run") -> MigrationResult | None:
    """Create `path` at the latest schema if it does not exist (returns the result). An existing
    file must already be current: older raises E002 (run `fin migrate`, which backs it up first),
    newer raises E004."""
    migrations = load_migrations(kind)
    if not path.exists():
        return _migrate_file(root, path, 0, migrations, run)
    current = current_version(path)
    if current > len(migrations):
        raise SchemaNewerThanCode(
            f"{_rel(root, path)} is at schema version {current}, but this fin knows only up to "
            f"{len(migrations)}; update fin (`git pull`, then `uv sync --frozen`)"
        )
    if current < len(migrations):
        raise DependencyVersionMismatch(
            f"{_rel(root, path)} is at schema version {current}, expected {len(migrations)}; "
            "run `uv run fin migrate`"
        )
    return None


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _wal(path: Path) -> Path:
    return path.with_name(path.name + ".wal")


def _backup(root: Path, path: Path, run: "Run") -> Path:
    backup = backups_dir(root) / run.run_id / path.relative_to(root)
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        if _wal(path).is_file():
            shutil.copy2(_wal(path), _wal(backup))
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot back up {path} to {backup}: {exc}") from exc
    return backup


def _restore(path: Path, backup: Path | None) -> None:
    for stale in (path, _wal(path)):
        stale.unlink(missing_ok=True)
    if backup is None:
        return
    shutil.copy2(backup, path)
    if _wal(backup).is_file():
        shutil.copy2(_wal(backup), _wal(path))


def _migrate_file(
    root: Path, path: Path, current: int, pending: list[Migration], run: "Run"
) -> MigrationResult:
    db_file = _rel(root, path)
    backup = _backup(root, path, run) if path.exists() else None
    backup_rel = None if backup is None else _rel(root, backup)
    target_version = pending[-1].version
    step = pending[0]
    try:
        with connect(path, write=True) as con:
            for step in pending:
                con.execute("BEGIN TRANSACTION")
                try:
                    con.execute(_SCHEMA_VERSION_DDL)
                    con.execute(step.sql)
                    con.execute(
                        "INSERT INTO schema_version VALUES (?, ?, ?, ?)",
                        [step.version, step.name, _utcnow(), run.run_id],
                    )
                    con.execute("COMMIT")
                except duckdb.Error:
                    con.execute("ROLLBACK")
                    raise
    except duckdb.Error as exc:
        cause = " ".join(str(exc).split())
        try:
            _restore(path, backup)
        except OSError as restore_exc:
            error = MigrationFailed(
                f"{db_file}: migration {step.name} failed ({cause}), and restoring "
                f"{backup_rel or 'the previous state'} failed too ({restore_exc}); "
                "restore it by hand"
            )
            _record(run, db_file, current, target_version, backup_rel, "failed", error)
            raise error from exc
        restored = f"restored from {backup_rel}" if backup_rel else "new file removed"
        error = MigrationFailed(f"{db_file}: migration {step.name} failed ({cause}); {restored}")
        _record(run, db_file, current, target_version, backup_rel, "failed", error)
        raise error from exc
    _record(run, db_file, current, target_version, backup_rel, "applied", None)
    return MigrationResult(db_file, current, target_version, backup_rel)


def _record(
    run: "Run",
    db_file: str,
    from_version: int,
    to_version: int,
    backup: str | None,
    status: str,
    error: MigrationFailed | None,
) -> None:
    run.record(
        "migrations",
        {
            "db_file": db_file,
            "from_version": from_version,
            "to_version": to_version,
            "backup_path": backup,
            "status": status,
            "error_code": None if error is None else error.code,
            "message": None if error is None else error.message,
        },
    )


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
