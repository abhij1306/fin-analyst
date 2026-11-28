"""`fin schema`: the views meant for questions, each with its columns and types (read from the
database), one line of meaning and one example query (the `-- about:` and `-- example:` comment
lines above the view in its schema file, or in `fin.statements.VIEW_SQL`). Also the hint E504 adds
when a query names a column that does not exist: the columns of the tables in the query.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from fin.db.conn import connect
from fin.db.schema import in_memory, schema_texts
from fin.paths import company_db_path
from fin.statements import VIEW_SQL, add_view

QUESTION_VIEWS = (
    "current_facts",
    "fact_sources",
    "standard_statements",
    "metrics",
    "metric_inputs",
    "metric_definitions",
    "document_facts",
)

_CREATE = re.compile(r"CREATE (?:OR REPLACE )?(?:TEMP )?VIEW (\w+)")
_NOTE = re.compile(r"-- (about|example): (.+)")
# A table named after FROM, JOIN, UPDATE or INTO; the column a binder error names.
_FROM = re.compile(r"\b(?:from|join|update|into)\s+\"?(\w+)", re.IGNORECASE)
_MISSING = re.compile(r'column (?:named )?"(\w+)"', re.IGNORECASE)


def notes() -> dict[str, dict[str, str]]:
    """view -> {"about": ..., "example": ...} from the comment lines right above each view."""
    found: dict[str, dict[str, str]] = {}
    for text in [*schema_texts("company"), VIEW_SQL]:
        block: dict[str, str] = {}
        for line in text.splitlines():
            if note := _NOTE.match(line):
                block[note[1]] = note[2].strip()
            elif create := _CREATE.match(line):
                found[create[1]] = block
            if not line.startswith("--"):
                block = {}
    return found


@contextmanager
def _database(root: Path, company: str | None) -> Iterator[duckdb.DuckDBPyConnection]:
    if company is None:
        with in_memory("company") as con:
            add_view(con, root)
            yield con
    else:
        with connect(company_db_path(root, company)) as con:
            add_view(con, root)
            yield con


def _columns(con: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, list[tuple[str, str]]]:
    rows = con.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_name IN (SELECT unnest(?)) ORDER BY table_name, ordinal_position",
        [tables],
    ).fetchall()
    found: dict[str, list[tuple[str, str]]] = {}
    for table, column, data_type in rows:
        found.setdefault(table, []).append((column, data_type))
    return found


def describe(
    root: Path, company: str | None = None, table: str | None = None
) -> list[dict[str, Any]]:
    """The question views (or one), in QUESTION_VIEWS order: view, about, example, columns. From
    the company's database, or an empty one made from the schema files."""
    views = [table] if table else list(QUESTION_VIEWS)
    with _database(root, company) as con:
        columns = _columns(con, views)
    meta = notes()
    return [
        {
            "view": view,
            "about": meta[view]["about"],
            "example": meta[view]["example"],
            "columns": [{"name": name, "type": kind} for name, kind in columns[view]],
        }
        for view in views
    ]


def format_schema(views: list[dict[str, Any]]) -> str:
    blocks = []
    for view in views:
        columns = ", ".join(f"{c['name']} {c['type']}" for c in view["columns"])
        blocks.append(f"{view['view']}: {view['about']}\n  {columns}\n  e.g. {view['example']}")
    return "\n\n".join(blocks)


def column_hint(con: duckdb.DuckDBPyConnection, sql: str, cause: str, company: str) -> str:
    """For an error naming a column: the columns of the tables in `sql`, the question views that
    have that column, and a pointer to `fin schema`. Empty for any other error."""
    missing = _MISSING.search(cause)
    if missing is None:
        return ""
    named = list(dict.fromkeys(t.lower() for t in _FROM.findall(sql)))
    have = _columns(con, named)
    parts = [f"{t} ({', '.join(c for c, _ in have[t])})" for t in named if t in have]
    hint = f". Columns of the tables in the query: {'; '.join(parts)}" if parts else ""
    column = missing[1].lower()
    holders = [
        t for t, cols in _columns(con, list(QUESTION_VIEWS)).items()
        if t not in named and any(c == column for c, _ in cols)
    ]  # fmt: skip
    if holders:
        hint += f". {column} is a column of {', '.join(sorted(holders))}"
    return f"{hint}. `uv run fin schema {company}` lists the views for questions and their columns"
