"""`fin delete <company>`: remove a company's data after backing it up (PLAN.md Phase 11).

Removed, relative to the data folder:
- `companies/<company>.duckdb` (and its `.wal`)
- `inbox/<company>/`, `archive/<company>/`, `review/<company>/`, `notes/<company>/`,
  `reports/<company>/`
- the evidence pages of the company's queries (`evidence/<query_id>.html` and its crops folder),
  found through the audit `queries` table

Everything removed is first copied to `backups/<run_id>/`, at the same relative paths. The audit
database is never touched except for one appended `deletions` row. Label mappings the company
confirmed stay in the shared dictionary.
"""

import json
import shutil
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable, DependencyMissing
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.metrics.export import refresh_analytics
from fin.paths import (
    archive_dir,
    audit_db_path,
    backups_dir,
    company_db_path,
    inbox_dir,
    notes_dir,
    reports_dir,
    review_dir,
)

COUNTED_TABLES = ("documents", "raw_values", "facts", "notes")


def company_paths(root: Path, company: str) -> list[Path]:
    """The files and folders that belong to `company`, as they exist now."""
    db = company_db_path(root, company)
    candidates = [
        db,
        db.with_name(f"{db.name}.wal"),
        inbox_dir(root, company),
        archive_dir(root, company),
        review_dir(root, company),
        notes_dir(root, company),
        reports_dir(root, company),
    ]
    with connect(audit_db_path(root)) as con:
        evidence = con.execute(
            "SELECT DISTINCT evidence_path FROM queries "
            "WHERE company = ? AND evidence_path IS NOT NULL",
            [company],
        ).fetchall()
    for (page,) in evidence:
        page_path = root / page
        candidates += [page_path, page_path.with_suffix("")]
    return [path for path in candidates if path.exists()]


def row_counts(root: Path, company: str) -> dict[str, int]:
    with connect(company_db_path(root, company)) as con:
        return {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in COUNTED_TABLES
        }


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def delete_company(run: Run, company: str) -> dict[str, object]:
    """Back up, remove and record. Returns the JSON payload."""
    db_path = company_db_path(run.root, company)
    if not db_path.is_file():
        raise DependencyMissing(
            f"no database for company {company!r} ({db_path}); nothing to delete"
        )
    paths = company_paths(run.root, company)
    counts = row_counts(run.root, company)
    backup = backups_dir(run.root) / run.run_id
    try:
        for path in paths:
            _copy(path, backup / path.relative_to(run.root))
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot back up {company} to {backup}: {exc}") from exc
    run.event("delete", f"{company}: backed up {len(paths)} paths to {backup}")

    removed: list[str] = []
    try:
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path.relative_to(run.root).as_posix())
    except OSError as exc:
        raise DataFolderNotWritable(
            f"cannot remove {path}: {exc}; removed so far: {removed}; backup in {backup}"
        ) from exc
    finally:
        run.record(
            "deletions",
            {
                "company": company,
                "files_removed": json.dumps(removed),
                "row_counts": json.dumps(counts),
                "backup_path": backup.relative_to(run.root).as_posix(),
            },
        )
    run.event("delete", f"{company}: removed {len(removed)} paths", context={"removed": removed})
    refresh_analytics(run)  # drops the company's rows from shared/analytics.duckdb
    return {
        "company": company,
        "files_removed": removed,
        "row_counts": counts,
        "backup_path": backup.relative_to(run.root).as_posix(),
    }
