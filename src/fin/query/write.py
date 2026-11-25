"""`fin sql <company> --write --sql "..."`: apply a data change the user asked for.

The company database is copied to `backups/<run_id>/companies/<company>.duckdb` first. The
statements then run in one transaction: all of them apply, or none. The audit `queries` table gets
the statement, the rows affected and the backup path (mode 'write'). Then every held document of the
company is checked again (`fin.findings.recheck_held`): a fix that clears its findings loads it.

A statement other than a SELECT that names a diligence table (engagement, accountant_queries,
adjustments, gate_approvals, the source file tables source_files, trial_balance,
ledger_entries, ledger_blocks, register_lines, payroll_lines, payroll_units, bank_statements,
bank_lines, passthrough_customers, contracts, samples, sample_lines, walkthroughs, gst_returns, the
FDD disclosures disclosure_files, disclosures and related_party_links, the ledger mappings
ledger_accounts and tax_ledgers, and the reconciliations recon_runs, recon_items,
query_items) is refused with E806 before the backup: those tables change only through
`fin engagement|query|adjust|gate`, `fin ingest --kind`, `fin map ledgers` with `fin review
--ledgers`, `fin reconcile`, `fin recast`, `fin sample` and `fin walkthrough`, which check the
gates and log every change. SQL naming
mask_values is refused with E810 (fin.query.run.refuse_masked). A statement that would end the
transaction or reach past the company database (BEGIN, COMMIT, ROLLBACK, ATTACH, DETACH, COPY FROM
DATABASE, LOAD/INSTALL) or change the schema (CREATE, DROP, ALTER, PRAGMA, SET, EXPORT, VACUUM) is
refused with E504, also before the backup. The statements run with file access closed
(`fin.db.conn.lock_files`).
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
    MaskedTableRead,
    SqlFailed,
)
from fin.audit.logger import Run
from fin.db.conn import connect, lock_files, no_company_message
from fin.findings import recheck_held
from fin.metrics.export import refresh_analytics
from fin.paths import backups_dir, company_db_path
from fin.query.run import refuse_masked

# Tables only the diligence commands change (`fin engagement|query|adjust|gate`).
DILIGENCE_TABLE = re.compile(
    r"\b(engagement|accountant_queries|adjustments|gate_approvals|source_files|trial_balance"
    r"|ledger_entries|ledger_blocks|register_lines|ledger_accounts|recon_runs|recon_items"
    r"|query_items|payroll_lines|payroll_units|bank_statements|bank_lines|passthrough_customers"
    r"|recon_match_lines|match_proposals|customers|contracts|samples|sample_lines|walkthroughs"
    r"|provision_ledgers|inquiries|orderbook_lines|gst_returns|tax_ledgers"
    r"|disclosure_files|disclosures|related_party_links"
    r"|netdebt_classes|netdebt_items|fdd_findings)\b"
)

# Statements that would end the wrapping transaction or reach past the company database.
REFUSED = {
    duckdb.StatementType.TRANSACTION: "BEGIN, COMMIT and ROLLBACK",
    duckdb.StatementType.ATTACH: "ATTACH",
    duckdb.StatementType.DETACH: "DETACH",
    duckdb.StatementType.COPY_DATABASE: "COPY FROM DATABASE",
    duckdb.StatementType.LOAD: "LOAD and INSTALL",
}

# Statements that change the schema or the session: a dropped view would stop every command (the
# database would read as an old format), so `fin sql --write` changes data only.
SCHEMA_CHANGE = {
    duckdb.StatementType.CREATE,
    duckdb.StatementType.CREATE_FUNC,
    duckdb.StatementType.DROP,
    duckdb.StatementType.ALTER,
    duckdb.StatementType.PRAGMA,
    duckdb.StatementType.SET,
    duckdb.StatementType.VARIABLE_SET,
    duckdb.StatementType.EXPORT,
    duckdb.StatementType.EXTENSION,
    duckdb.StatementType.VACUUM,
}

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


def check_write_sql(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Check every statement before any runs. E504 when the SQL does not parse or a statement is
    one of REFUSED; E806 when a statement other than a SELECT names a diligence table (a name match
    is enough: when in doubt, refuse); E810 when the SQL names mask_values."""
    refuse_masked(sql)
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlFailed(f"cannot parse the SQL: {' '.join(str(exc).split())}") from exc
    for statement in statements:
        if statement.type in REFUSED:
            raise SqlFailed(
                f"`fin sql --write` runs the SQL in its own transaction on the company database; "
                f"{REFUSED[statement.type]} are not allowed. Nothing was run"
            )
        if statement.type == duckdb.StatementType.SELECT:
            continue
        named = sorted(set(DILIGENCE_TABLE.findall((statement.query or sql).lower())))
        if named:
            raise DiligenceTableWrite(
                f"`fin sql --write` does not change the diligence tables (found: "
                f"{', '.join(named)}); use `fin engagement`, `fin query`, `fin adjust`, "
                "`fin gate` or `fin ingest --kind`. Nothing was run"
            )
        if statement.type in SCHEMA_CHANGE:
            raise SqlFailed(
                f"schema changes are not allowed ({statement.type.name}): `fin sql --write` "
                "changes data only; restore from backups/<run_id> if a company database was "
                "changed. Nothing was run"
            )


def write_sql(run: Run, company: str, sql: str, question: str | None = None) -> dict[str, Any]:
    """Back up the company database, run `sql` in one transaction and log it. Returns the JSON
    payload. A failing statement rolls everything back and raises E504. SQL that `check_write_sql`
    refuses (E504, E806) is logged before anything runs."""
    path = company_db_path(run.root, company)
    if not path.is_file():
        raise DependencyMissing(no_company_message(company))
    with connect(path) as con:
        try:
            check_write_sql(con, sql)
        except (DiligenceTableWrite, MaskedTableRead, SqlFailed):
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
        lock_files(con)
        con.execute("BEGIN TRANSACTION")
        try:
            for statement in con.extract_statements(sql):
                rows_affected += _changed_rows(con, statement)
            con.execute("COMMIT")
        except duckdb.Error as exc:
            cause = " ".join(str(exc).split())
            try:
                con.execute("ROLLBACK")
            except duckdb.Error as rollback:
                raise SqlFailed(
                    f"the change failed ({cause}) and rolling it back failed too "
                    f"({' '.join(str(rollback).split())}); the backup is {saved}"
                ) from exc
            raise SqlFailed(f"the change failed and was rolled back: {cause}") from exc
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
