"""`fin sql <company> --write --sql "..."`: apply a data change the user asked for.

The company database is copied to `backups/<run_id>/companies/<company>.duckdb` first. The
statements then run in one transaction: all of them apply, or none. The audit `queries` table gets
the statement, the rows affected and the backup path (mode 'write'). Then every held document of the
company is checked again (`fin.findings.recheck_held`): a fix that clears its findings loads it.

A statement other than a SELECT that names a diligence table (engagement, accountant_queries,
adjustments, gate_approvals, the source file tables source_files, trial_balance,
ledger_entries, ledger_blocks, register_lines, payroll_lines, payroll_units, bank_statements,
bank_lines, passthrough_customers, contracts, samples, sample_lines, walkthroughs, gst_returns, the
FDD tables provision_ledgers, inquiries, orderbook_lines, engagement_rates, disclosure_files,
disclosures,
related_party_links, netdebt_classes, netdebt_items and fdd_findings, the ledger mappings
ledger_accounts and tax_ledgers, and the reconciliations recon_runs, recon_items,
query_items) is refused with E806 before the backup: those tables change only through
`fin engagement|query|adjust|gate`, `fin ingest --kind`, `fin map ledgers` with `fin review
--ledgers`, `fin reconcile`, `fin recast`, `fin sample`, `fin walkthrough` and the FDD commands,
which check the gates and log every change. A statement other than a SELECT that names facts,
raw_values, stated_figures, fact_corrections or documents is refused with E505, also before the
backup: figures come from documents (`fin ingest`, `fin review`) or from the user (`fin fact
state`), a figure fin misread is corrected with `fin fact correct`, and a document is loaded, held,
released or removed only by `fin ingest`, `fin findings accept`, `fin fact correct` and `fin
document delete` (owner, 2026-11-28: a status written by hand released a held document). SQL naming
mask_values is refused with E810 (fin.query.run.refuse_masked). A statement that would end the
transaction or reach past the company database (BEGIN, COMMIT, ROLLBACK, ATTACH, DETACH, COPY FROM
DATABASE, LOAD/INSTALL) or change the schema (CREATE, DROP, ALTER, PRAGMA, SET, EXPORT, VACUUM) is
refused with E504, also before the backup, as is a change naming the saved metric tables
(`fin metric`). The statements run with file access closed
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
    FigureTableWrite,
    MaskedTableRead,
    SqlFailed,
)
from fin.audit.logger import Run
from fin.db.conn import connect, lock_files, no_company_message
from fin.findings import recheck_held
from fin.metrics.export import refresh_analytics
from fin.paths import backups_dir, company_db_path
from fin.query.guide import column_hint
from fin.query.run import refuse_masked

# Tables only the diligence commands change (`fin engagement|query|adjust|gate`).
DILIGENCE_TABLE = re.compile(
    r"\b(engagement|accountant_queries|adjustments|gate_approvals|source_files|trial_balance"
    r"|ledger_entries|ledger_blocks|register_lines|ledger_accounts|recon_runs|recon_items"
    r"|query_items|payroll_lines|payroll_units|bank_statements|bank_lines|passthrough_customers"
    r"|recon_match_lines|match_proposals|customers|contracts|samples|sample_lines|walkthroughs"
    r"|provision_ledgers|inquiries|orderbook_lines|engagement_rates|gst_returns|tax_ledgers"
    r"|disclosure_files|disclosures|related_party_links"
    r"|netdebt_classes|netdebt_items|fdd_findings)\b"
)

# The figures: loaded by `fin ingest`, mapped by `fin review`, stated by `fin fact state`, never
# written by hand (SPEC-08: an assistant once inserted zero rows as if read from a document).
# And the documents behind them (owner, 2026-11-28): a status written by hand would release a
# held document into current_facts without its findings accepted, and its stored paths are read
# and deleted.
FIGURE_TABLE = re.compile(r"\b(facts|raw_values|stated_figures|fact_corrections|documents)\b")
# Saved metrics change only through `fin metric add|remove`, which rewrites the view computing them.
SAVED_METRIC_TABLE = re.compile(r"\bsaved_metric(?:s|_operands)\b")

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
    is enough: when in doubt, refuse); E505 when one names a figure table or documents
    (FIGURE_TABLE; checked after every statement passed the others); E810 when the SQL names
    mask_values."""
    refuse_masked(sql)
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlFailed(f"cannot parse the SQL: {' '.join(str(exc).split())}") from exc
    for statement in statements:
        _refuse_statement(statement, sql)
    for statement in statements:
        if statement.type == duckdb.StatementType.SELECT:
            continue
        named = sorted(set(FIGURE_TABLE.findall((statement.query or sql).lower())))
        if named:
            raise FigureTableWrite(
                f"`fin sql --write` does not change the figures or the documents (found: "
                f"{', '.join(named)}). Load a document (`fin ingest`, `--reading` for pages fin "
                "could not read), map its lines (`fin review`), state a figure the documents do "
                "not give (`fin fact state`), correct one fin misread (`fin fact correct`), "
                "accept a held document's findings (`fin findings accept`) or delete a document "
                "(`fin document delete`). Nothing was run"
            ).with_context(tables=named)


def _refuse_statement(statement: Any, sql: str) -> None:
    """E504 or E806 for one statement of `fin sql --write` (check_write_sql)."""
    if statement.type in REFUSED:
        raise SqlFailed(
            f"`fin sql --write` runs the SQL in its own transaction on the company database; "
            f"{REFUSED[statement.type]} are not allowed. Nothing was run"
        )
    if statement.type == duckdb.StatementType.SELECT:
        return
    named = sorted(set(DILIGENCE_TABLE.findall((statement.query or sql).lower())))
    if named:
        raise DiligenceTableWrite(
            f"`fin sql --write` does not change the diligence tables (found: "
            f"{', '.join(named)}); use `fin engagement`, `fin query`, `fin adjust`, "
            "`fin gate` or `fin ingest --kind`. Nothing was run"
        )
    if SAVED_METRIC_TABLE.search((statement.query or sql).lower()):
        raise SqlFailed(
            "`fin sql --write` does not change saved metrics; use `fin metric add` or "
            "`fin metric remove`. Nothing was run"
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
        except (DiligenceTableWrite, FigureTableWrite, MaskedTableRead, SqlFailed):
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
            hint = column_hint(con, sql, cause, company)
            raise SqlFailed(f"the change failed and was rolled back: {cause}{hint}") from exc
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
