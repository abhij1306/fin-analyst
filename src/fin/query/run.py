"""`fin query`: answer a question with one read-only SELECT on a company database (PLAN.md Phase 8).

The statement must be a single SELECT (or WITH ... SELECT); anything else is rejected with E502 and
still logged. When the result has a `fact_id` or `raw_id` column, every row gets provenance columns:
the source file, `sheet!cell` or `page N`, whether the fact's mapping is confirmed, who read the
figure (`fin`, or `claude` from a reading file) and whether `fin verify` verified it.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import ReadOnlyViolation, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.paths import company_db_path
from fin.statements import add_view

PROVENANCE_COLUMNS = (
    "source_file",
    "source_ref",
    "source_confirmed",
    "source_read_by",
    "source_verified",
)

# Where each raw value came from. `source_ref` is `sheet!cell` for Excel and `page N` for PDF.
SOURCE_SQL = """
SELECT r.raw_id, f.fact_id, d.file_name AS source_file,
       source_ref(r.sheet, r.cell, r.page) AS source_ref,
       f.confirmed AS source_confirmed, f.read_by AS source_read_by,
       f.verified AS source_verified
FROM raw_values r
JOIN documents d ON d.doc_id = r.doc_id
LEFT JOIN facts f ON f.raw_id = r.raw_id
"""


def check_select(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Raise E502 unless `sql` is exactly one SELECT statement (WITH ... SELECT counts)."""
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlFailed(f"cannot parse the SQL: {exc}") from exc
    kinds = [statement.type for statement in statements]
    if kinds != [duckdb.StatementType.SELECT]:
        found = ", ".join(kind.name for kind in kinds) or "nothing"
        raise ReadOnlyViolation(
            f"`fin query` runs one SELECT statement only (found: {found}); "
            "to change data use `fin sql <company> --write --sql ...`"
        )


def run_select(root: Path, company: str, sql: str) -> list[dict[str, Any]]:
    """Run one SELECT on a read-only connection and return the rows, with provenance columns added
    when the result has `fact_id` or `raw_id`. The `standard_statements` view (fin.statements) is
    available to it."""
    with connect(company_db_path(root, company)) as con:
        check_select(con, sql)
        add_view(con, root)  # standard_statements
        try:
            rows = fetch_dicts(con, sql)
        except duckdb.Error as exc:
            raise SqlFailed(f"the query failed: {' '.join(str(exc).split())}") from exc
        columns = rows[0] if rows else {}
        key = "fact_id" if "fact_id" in columns else "raw_id" if "raw_id" in columns else None
        if key is None:
            return rows
        sources = {}
        for source in con.execute(SOURCE_SQL).fetchall():
            raw_id, fact_id, *provenance = source
            sources[fact_id if key == "fact_id" else raw_id] = dict(
                zip(PROVENANCE_COLUMNS, provenance, strict=True)
            )
    empty = dict.fromkeys(PROVENANCE_COLUMNS)
    return [{**row, **sources.get(row[key], empty)} for row in rows]


def _plain(value: object) -> object:
    """JSON form of a DuckDB value: exact decimals as strings, dates as ISO text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def to_json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{column: _plain(value) for column, value in row.items()} for row in rows]


def result_hash(rows: list[dict[str, Any]]) -> str:
    text = json.dumps(to_json_rows(rows), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def query(run: Run, company: str, question: str, sql: str) -> dict[str, Any]:
    """Run the question's SQL, log it to the audit `queries` table and return the JSON payload.
    A rejected or failed statement is logged too, with no row count, before the error is raised."""
    row = {
        "query_id": run.run_id,
        "company": company,
        "question": question,
        "sql": sql,
        "mode": "read",
        "row_count": None,
        "result_hash": None,
    }
    try:
        rows = run_select(run.root, company, sql)
    except (ReadOnlyViolation, SqlFailed):
        run.record("queries", row)
        raise
    digest = result_hash(rows)
    run.record("queries", row | {"row_count": len(rows), "result_hash": digest})
    return {
        "query_id": run.run_id,
        "company": company,
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "result_hash": digest,
        "rows": to_json_rows(rows),
    }
