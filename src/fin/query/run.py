"""`fin sql` without --write: answer a question with one read-only SELECT on a company database.

The statement must be a single SELECT (or WITH ... SELECT); anything else is rejected with E502 and
still logged. When the result has a `fact_ids`, `fact_id` or `raw_id` column (the first one found),
every row gets provenance columns: the source file, `sheet!cell` or `page N`, whether the fact's
mapping is confirmed, who read the figure (`fin`; `docling`, `liteparse` or `llamaparse` from the
PDF add-on; or `claude` from a reading file).

`fact_ids` (in `current_facts`, `document_facts` and `standard_statements`) lists every fact behind
a value. A value summed from several printed lines gets every line's source: `source_ref` starts
with "sum of N sources:", and the value counts as confirmed only when every line is.

A row with a `stated_id` (`current_facts`, `metric_inputs`, `standard_statements`) is a figure the
user stated where no document gives it (`fin fact state`): its source is "stated by the user", its
reference the stated figure's id and reason, read by `user`. A figure corrected with `fin fact
correct` keeps its cell or page, with ", corrected #id from <value before>" after it.
"""

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import MaskedTableRead, ReadOnlyViolation, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect, lock_files
from fin.db.rows import fetch_dicts
from fin.discrepancies.view import add_view as add_discrepancies
from fin.paths import company_db_path
from fin.query.evidence import write_evidence
from fin.query.guide import column_hint
from fin.rebuilt.view import add_views

# The source_ref of a line in a sum whose raw value records no sheet, cell or page.
NO_SOURCE = "no source"

PROVENANCE_COLUMNS = (
    "source_file",
    "source_ref",
    "source_confirmed",
    "source_read_by",
)

# Where each raw value came from. `source_ref` is `sheet!cell` for Excel and `page N` for PDF.
SOURCE_SQL = """
SELECT r.raw_id, f.fact_id, d.file_name AS source_file,
       source_ref(r.sheet, r.cell, r.page)
       || coalesce(', corrected #' || c.correction_id || ' from ' || c.original_value, '')
           AS source_ref,
       f.confirmed AS source_confirmed, f.read_by AS source_read_by
FROM raw_values r
JOIN documents d ON d.doc_id = r.doc_id
LEFT JOIN facts f ON f.raw_id = r.raw_id
LEFT JOIN corrected_now c ON c.fact_id = f.fact_id
"""


# The real values behind masked tokens (fin.diligence.mask): fin never prints them. Also refused:
# the table functions that take a table name, SQL or a database file as text, which could reach
# mask_values without naming it ("query_table('mask_' || 'values')").
MASKED_TABLE = re.compile(
    r'\bmask_values\b|\b(?:query|query_table|read_duckdb)"?\s*\(', re.IGNORECASE
)


# Table functions that read a table or a database named as text; a call to `query` is refused,
# and the other two names wherever they appear.
READS_BY_NAME = ("query_table", "read_duckdb")


def _reaches_masked(sql: str) -> bool:
    """Whether DuckDB's own tokens, comments left out, name mask_values, call query, or name
    query_table or read_duckdb: a comment is whitespace to DuckDB (`query_table/**/(`), so the
    text alone cannot tell. SQL the tokenizer cannot read counts as reaching it."""
    try:
        tokens = duckdb.tokenize(sql)
    except duckdb.Error:
        return True
    words = []
    ends = [start for start, _ in tokens[1:]] + [len(sql)]
    for (start, kind), end in zip(tokens, ends, strict=True):
        if kind != duckdb.token_type.comment:
            words.append(sql[start:end].strip().strip('"').lower())
    for word, after in zip(words, [*words[1:], ""], strict=True):
        name = word.rsplit(".", 1)[-1]
        if name == "mask_values" or name in READS_BY_NAME:
            return True
        if name == "query" and after.startswith("("):
            return True
    return False


def refuse_masked(sql: str) -> None:
    """E810 when the SQL names `mask_values` or calls query, query_table or read_duckdb, in the
    text or in DuckDB's tokens with comments left out (a name match is enough: when in doubt,
    refuse)."""
    if MASKED_TABLE.search(sql) or _reaches_masked(sql):
        raise MaskedTableRead(
            "`fin sql` does not read mask_values (nor run query, query_table or read_duckdb, "
            "which could reach it): it holds the real names behind the masked tokens, which fin "
            "never prints. Work with the tokens. Nothing was run"
        )


def check_select(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Raise E810 when `sql` names mask_values (or a function that could reach it), and E502
    unless it is exactly one SELECT statement (WITH ... SELECT counts)."""
    refuse_masked(sql)
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlFailed(f"cannot parse the SQL: {exc}") from exc
    kinds = [statement.type for statement in statements]
    if kinds != [duckdb.StatementType.SELECT]:
        found = ", ".join(kind.name for kind in kinds) or "nothing"
        raise ReadOnlyViolation(
            f"without --write, `fin sql` runs one SELECT statement only (found: {found}); "
            "add --write only to change data the user asked to change"
        )


def run_select(root: Path, company: str, sql: str) -> list[dict[str, Any]]:
    """Run one SELECT on a read-only connection and return the rows, with provenance columns added
    when the result has `fact_ids`, `fact_id` or `raw_id`. The `standard_statements` view
    (fin.statements), the `rebuilt_statements` view (fin.rebuilt) and the `discrepancies` view
    (fin.discrepancies) are available to it."""
    with connect(company_db_path(root, company)) as con:
        check_select(con, sql)
        add_views(con, root)  # standard_statements, rebuilt_statements
        add_discrepancies(con)  # discrepancies
        lock_files(con)
        try:
            rows = fetch_dicts(con, sql)
        except duckdb.Error as exc:
            cause = " ".join(str(exc).split())
            hint = column_hint(con, sql, cause, company)
            raise SqlFailed(f"the query failed: {cause}{hint}") from exc
        columns = rows[0] if rows else {}
        key = next((name for name in ("fact_ids", "fact_id", "raw_id") if name in columns), None)
        if key is None and "stated_id" not in columns:
            return rows
        stated = {}
        if "stated_id" in columns:  # only ids of real stated figures are marked as stated
            stated = dict(con.execute("SELECT stated_id, reason FROM stated_figures").fetchall())
        sources = {}
        for source in con.execute(SOURCE_SQL).fetchall() if key else []:
            raw_id, fact_id, *provenance = source
            source_key = raw_id if key == "raw_id" else fact_id
            if source_key is None:
                continue  # a raw value mapped to no fact: no row's fact_id points to it
            sources[source_key] = dict(zip(PROVENANCE_COLUMNS, provenance, strict=True))
    return _with_sources(rows, key, stated, sources)


def _with_sources(
    rows: list[dict[str, Any]],
    key: str | None,
    stated: dict[Any, str],
    sources: dict[Any, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Each row with the provenance columns of its stated figure, facts or raw value."""
    empty: dict[str, Any] = dict.fromkeys(PROVENANCE_COLUMNS)
    out = []
    for row in rows:
        if row.get("stated_id") in stated:
            out.append(row | stated_source(row["stated_id"], stated[row["stated_id"]]))
        elif key is None:
            out.append(row | empty)
        elif key == "fact_ids":
            out.append(row | _from_ids(sources, row[key], empty))
        else:
            out.append(row | sources.get(row[key], empty))
    return out


def stated_source(stated_id: int, reason: str) -> dict[str, Any]:
    """The provenance of a figure the user stated: no document, the user's reason."""
    return {
        "source_file": "stated by the user",
        "source_ref": f"stated #{stated_id}: {reason}",
        "source_confirmed": None,
        "source_read_by": "user",
    }


def _from_ids(
    sources: dict[Any, dict[str, Any]], ids: list[Any] | None, empty: dict[str, Any]
) -> dict[str, Any]:
    """The provenance of a `fact_ids` value: NULL ids (a list over a LEFT JOIN) are skipped; an
    id that is no fact gives empty provenance, as for `fact_id`."""
    known = [i for i in ids or [] if i is not None]
    if any(i not in sources for i in known):
        return empty
    return _combined([sources[i] for i in known])


def _combined(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The provenance of a value from `lines` (one source each): a single line's as it is; for a
    sum, every line's source, confirmed only when every line is."""
    if len(lines) <= 1:
        return lines[0] if lines else dict.fromkeys(PROVENANCE_COLUMNS)

    def joined(column: str) -> str:
        return "; ".join(dict.fromkeys(str(line[column]) for line in lines))

    return {
        "source_file": joined("source_file"),
        "source_ref": f"sum of {len(lines)} sources: "
        + "; ".join(line["source_ref"] or NO_SOURCE for line in lines),
        "source_confirmed": all(line["source_confirmed"] for line in lines),
        "source_read_by": joined("source_read_by"),
    }


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


def query(
    run: Run, company: str, question: str | None, sql: str, evidence: bool = False
) -> dict[str, Any]:
    """Run the SQL, write its evidence page if asked, log it to the audit `queries` table and
    return the JSON payload. A rejected or failed statement is logged too, with no row count,
    before the error is raised."""
    row: dict[str, Any] = {
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
    except (MaskedTableRead, ReadOnlyViolation, SqlFailed):
        run.record("queries", row)
        raise
    row |= {"row_count": len(rows), "result_hash": result_hash(rows)}
    unsourced: list[int] = []
    if evidence:
        row["evidence_path"], unsourced = write_evidence(
            run, company, question, sql, to_json_rows(rows)
        )
    run.record("queries", row)
    # The result rows (numbered from 1) the evidence page has no source for.
    extra = {"rows_without_source": unsourced} if unsourced else {}
    return {**row, **extra, "rows": to_json_rows(rows)}
