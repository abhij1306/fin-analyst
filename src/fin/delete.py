"""`fin delete <company>`: remove a company's data after backing it up (docs/product.md).

Removed, relative to the data folder:
- `companies/<company>.duckdb` (and its `.wal`)
- `inbox/<company>/`, `archive/<company>/`, `review/<company>/`, `notes/<company>/`,
  `reports/<company>/`, `exports/<company>/`, `evals/<company>/`
- `cache/bank/<company>/`, the saved parses of its bank PDFs (they hold its account tokens)
- the saved PDF parses of the company's files (`cache/<engine>/<sha256>...`, found through the
  sha256 of its documents and source files; not those of a file another company loaded too)
- the evidence pages of the company's queries (`evidence/<query_id>.html` and its crops folder),
  found through the audit `queries` table

Everything removed is first copied to `backups/<run_id>/`, at the same relative paths. The audit
database is never touched except for one appended `deletions` row. Label mappings the company
confirmed stay in the shared dictionary (the output says how many).
"""

import json
import shutil
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable, DependencyMissing
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.rows import fetch_one
from fin.metrics.export import refresh_analytics
from fin.paths import (
    archive_dir,
    audit_db_path,
    backups_dir,
    companies_dir,
    company_db_path,
    company_evals_dir,
    dictionary_db_path,
    exports_dir,
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
        exports_dir(root, company),
        company_evals_dir(root, company),
        root / "cache" / "bank" / company,  # saved bank parses hold the company's tokens
        *_cache_files(root, company),
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


def _shas(db: Path) -> set[str]:
    """The sha256 of every document and source file in a company database."""
    with connect(db) as con:
        return {
            sha
            for table in ("documents", "source_files")
            for (sha,) in con.execute(f"SELECT DISTINCT sha256 FROM {table}").fetchall()  # noqa: S608
        }


def _cache_files(root: Path, company: str) -> list[Path]:
    """The saved parses (`cache/<engine>/<sha256>*`) of the company's documents and source files,
    except those of a file another company has loaded too (a parse is keyed by the file's content
    alone, so the other company still uses it)."""
    shas = _shas(company_db_path(root, company))
    for other in companies_dir(root).glob("*.duckdb"):
        if other != company_db_path(root, company):
            shas -= _shas(other)
    cache = root / "cache"
    if not cache.is_dir():
        return []
    return sorted(p for sha in shas for p in cache.glob(f"*/{sha}*") if p.is_file())


def company_mappings(root: Path, company: str) -> int:
    """The company-scope label mappings in the shared dictionary (kept on delete)."""
    with connect(dictionary_db_path(root)) as con:
        row = con.execute(
            "SELECT count(*) FROM label_map WHERE scope = 'company' AND company = ?", [company]
        ).fetchone()
    return row[0] if row else 0


def row_counts(root: Path, company: str) -> dict[str, int]:
    with connect(company_db_path(root, company)) as con:
        return {
            table: fetch_one(con, f"SELECT count(*) FROM {table}")[0]  # noqa: S608
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
    mappings = company_mappings(run.root, company)
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
        "note": f"{mappings} company-scope label mappings stay in shared/dictionary.duckdb "
        "(a new company of the same name would use them)",
    }
