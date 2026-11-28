"""Deleting one document of a company (the Files screen, docs/specs/SPEC-03).

A document is a file with all its versions (the rows of `documents` with its sha256). Deleting it
removes their rows from every company table with a `doc_id` (raw values, facts, notes, findings,
verify runs and their lines) in one transaction, and its files: the archived original, a reading
no other document uses, its notes and the inbox copy with the same content. Everything is first
copied to `backups/<run_id>/` at the same relative paths, the company database included; the
audit database keeps its rows and gets one `deletions` row. A gate whose inputs change reopens on
its own (`fin.diligence.gates`)."""

import json
import shutil
from pathlib import Path

import duckdb

from fin.audit.errors import DataFolderNotWritable, DependencyMissing
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.findings import recheck_held
from fin.ingest_read import sha256_of
from fin.map.review import write_pending
from fin.metrics.export import refresh_analytics
from fin.paths import backups_dir, company_db_path, inbox_dir, inside

TABLES_WITH_DOC_ID = (
    "SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t "
    "USING (table_name) WHERE c.column_name = 'doc_id' AND t.table_type = 'BASE TABLE' "
    "ORDER BY c.table_name"
)


def _versions(con: duckdb.DuckDBPyConnection, company: str, doc_id: int) -> list[tuple]:
    """(doc_id, file_name, sha256, archive_path, reading_path) of every version of the file;
    E001 when there is no such document."""
    rows = con.execute(
        "SELECT doc_id, file_name, sha256, archive_path, reading_path FROM documents "
        "WHERE sha256 = (SELECT sha256 FROM documents WHERE doc_id = ?) ORDER BY doc_id",
        [doc_id],
    ).fetchall()
    if not rows:
        raise DependencyMissing(f"{company} has no document {doc_id}; nothing deleted")
    return rows


def _files(root: Path, company: str, con: duckdb.DuckDBPyConnection, versions: list[tuple]):
    """The files of the document that exist, relative to the data folder."""
    ids = [v[0] for v in versions]
    marks = ", ".join("?" for _ in ids)
    readings = {v[4] for v in versions if v[4]} - {
        path
        for (path,) in con.execute(
            f"SELECT reading_path FROM documents WHERE doc_id NOT IN ({marks})", ids
        ).fetchall()
    }
    notes = con.execute(f"SELECT markdown_path FROM notes WHERE doc_id IN ({marks})", ids)
    found = {v[3] for v in versions} | readings | {path for (path,) in notes.fetchall()}
    inbox = inbox_dir(root, company)
    for name in {v[1] for v in versions}:
        copy = inbox / name
        inbox_copy = copy.is_file() and copy.resolve().parent == inbox.resolve()
        if inbox_copy and sha256_of(copy) == versions[0][2]:
            found.add(copy.relative_to(root).as_posix())
    # paths from database rows: each must stay in the data folder before it is copied or removed
    return sorted(path for path in found if inside(root, root / path).is_file())


def _delete_rows(con: duckdb.DuckDBPyConnection, ids: list[int], sha256: str) -> dict[str, int]:
    """Delete the rows of the documents from every table with a doc_id; returns the count per
    table (verify_lines through their verify run). A data-room plan row of the file that says
    loaded is set back to planned and not approved (`dataroom_files`)."""
    marks = ", ".join("?" for _ in ids)
    counts = {}
    con.execute("BEGIN TRANSACTION")
    try:
        runs = f"SELECT run_id FROM verify_runs WHERE doc_id IN ({marks})"
        counts["verify_lines"] = len(
            con.execute(f"DELETE FROM verify_lines WHERE run_id IN ({runs}) RETURNING 1", ids)
            .fetchall()
        )  # fmt: skip
        tables = [t for (t,) in con.execute(TABLES_WITH_DOC_ID).fetchall() if t != "documents"]
        for table in [*tables, "documents"]:  # names from the schema, not from input
            deleted = con.execute(
                f"DELETE FROM {table} WHERE doc_id IN ({marks}) RETURNING 1",
                ids,
            ).fetchall()
            counts[table] = len(deleted)
        counts["dataroom_files"] = len(
            con.execute(
                "UPDATE dataroom_files SET status = 'planned', approved = false, outcome = NULL, "
                "load_run_id = NULL WHERE sha256 = ? AND status = 'loaded' RETURNING 1",
                [sha256],
            ).fetchall()
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return counts


def delete_document(run: Run, company: str, doc_id: int) -> list[str]:
    """Back up, delete and record one document with all its versions; the lines to show."""
    db = company_db_path(run.root, company)
    if not db.is_file():
        raise DependencyMissing(f"no database for company {company!r}; nothing to delete")
    with connect(db) as con:
        versions = _versions(con, company, doc_id)
        files = _files(run.root, company, con, versions)
    backup = backups_dir(run.root) / run.run_id
    try:
        for path in [db.relative_to(run.root).as_posix(), *files]:
            (backup / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run.root / path, backup / path)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot back up {company} to {backup}: {exc}") from exc
    ids = [v[0] for v in versions]
    with connect(db, write=True) as con:
        counts = _delete_rows(con, ids, versions[0][2])
    try:
        for path in files:
            (run.root / path).unlink()
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot remove {path}: {exc}; backup in {backup}") from exc
    finally:
        run.record(
            "deletions",
            {
                "company": company,
                "files_removed": json.dumps(files),
                "row_counts": json.dumps(counts),
                "backup_path": backup.relative_to(run.root).as_posix(),
            },
        )
    name = versions[-1][1]
    run.event("delete", f"{company}/{name}: deleted doc_id {ids}", context={"removed": files})
    write_pending(run, company)  # its labels leave the Mappings screen
    # a document held by a comparison with the deleted one is checked again without it
    rechecked = recheck_held(run, company)
    refresh_analytics(run)
    return [
        f"deleted {name} (doc_id {', '.join(map(str, ids))}): {counts['facts']} figures, "
        f"{counts['findings']} findings",
        f"removed {len(files)} files: {', '.join(files) or 'none'}",
        f"backup in {backup.relative_to(run.root).as_posix()}/",
        *(
            f"held document rechecked: doc_id {doc['doc_id']} {doc['file']} is {doc['status']}"
            for doc in rechecked
        ),
    ]
