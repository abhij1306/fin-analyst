"""`fin sql <company> --write --sql "..."`: apply a data change the user asked for.

The company database is copied to `backups/<run_id>/companies/<company>.duckdb` first. The
statements then run in one transaction: all of them apply, or none. The audit `queries` table gets
the statement, the rows affected and the backup path (mode 'write'). Then every held document of the
company is checked again (`fin.findings.recheck_held`): a fix that clears its findings loads it.

A statement other than a SELECT that names a diligence table (engagement, accountant_queries,
adjustments, gate_approvals) is refused with E806 before the backup: those tables change only
through `fin engagement|query|adjust|gate`, which check the gates and log every change.
"""

import re
import shutil
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import (
    DataFolderNotWritable,
    DependencyMissing,
    DiligenceTableWrite,
    SqlFailed,
)
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.findings import recheck_held
from fin.metrics.export import refresh_analytics
from fin.paths import backups_dir, company_db_path

# Tables only the diligence commands change (`fin engagement|query|adjust|gate`).
DILIGENCE_TABLE = re.compile(r"\b(engagement|accountant_queries|adjustments|gate_approvals)\b")

COUNTED = {
    duckdb.StatementType.INSERT,
    duckdb.StatementType.UPDATE,
    duckdb.StatementType.DELETE,
}


def backup(root: Path, path: Path, run: Run) -> Path:
    """Copy `path` (and its WAL file, if any) to `backups/<run_id>/<path in data folder>`."""
    target = backups_dir(root) / run.run_id / path.relative_to(root)
    wal = path.with_name(path.name + ".wal")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if wal.is_file():
            shutil.copy2(wal, target.with_name(target.name + ".wal"))
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot back up {path} to {target}: {exc}") from exc
    return target


def _changed_rows(con: duckdb.DuckDBPyConnection, statement: Any) -> int:
    """Run `statement`; the rows it changed. DuckDB returns one `Count` column for INSERT, UPDATE
    and DELETE, but the returned rows instead when the statement has a RETURNING clause."""
    result = con.execute(statement).fetchall()
    if statement.type not in COUNTED:
        return 0
    columns = [column[0] for column in con.description or []]
    if columns == ["Count"] and len(result) == 1:
        return int(result[0][0])
    return len(result)


def check_not_diligence(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """E806 when a statement other than a SELECT names a diligence table. A name match is enough:
    when in doubt, refuse. E504 when the SQL does not parse."""
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlFailed(f"cannot parse the SQL: {' '.join(str(exc).split())}") from exc
    for statement in statements:
        if statement.type == duckdb.StatementType.SELECT:
            continue
        named = sorted(set(DILIGENCE_TABLE.findall((statement.query or sql).lower())))
        if named:
            raise DiligenceTableWrite(
                f"`fin sql --write` does not change the diligence tables (found: "
                f"{', '.join(named)}); use `fin engagement`, `fin query`, `fin adjust` or "
                "`fin gate`. Nothing was run"
            )


def write_sql(run: Run, company: str, sql: str, question: str | None = None) -> dict[str, Any]:
    """Back up the company database, run `sql` in one transaction and log it. Returns the JSON
    payload. A failing statement rolls everything back and raises E504. A statement that writes to
    a diligence table is refused (E806) and logged before anything runs."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        raise DependencyMissing(f"no database for company {company!r} ({path}); ingest first")
    with connect(path) as con:
        try:
            check_not_diligence(con, sql)
        except (DiligenceTableWrite, SqlFailed):
            run.record(
                "queries",
                {
                    "query_id": run.run_id,
                    "company": company,
                    "question": question,
                    "sql": sql,
                    "mode": "write",
                    "row_count": None,
                },
            )
            raise
    saved = backup(run.root, path, run)
    rows_affected = 0
    with connect(path, write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for statement in con.extract_statements(sql):
                rows_affected += _changed_rows(con, statement)
            con.execute("COMMIT")
        except duckdb.Error as exc:
            con.execute("ROLLBACK")
            raise SqlFailed(
                f"the change failed and was rolled back: {' '.join(str(exc).split())}"
            ) from exc
    backup_path = saved.relative_to(run.root).as_posix()
    run.record(
        "queries",
        {
            "query_id": run.run_id,
            "company": company,
            "question": question,
            "sql": sql,
            "mode": "write",
            "row_count": rows_affected,
            "backup_path": backup_path,
        },
    )
    held = recheck_held(run, company)
    refresh_analytics(run)  # metrics are views; this rebuilds the dashboards' shared copy
    return {
        "query_id": run.run_id,
        "company": company,
        "sql": sql,
        "rows_affected": rows_affected,
        "backup_path": backup_path,
        "held_documents_rechecked": held,
    }
