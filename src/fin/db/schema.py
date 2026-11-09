"""Creating the databases from their schema files (PLAN.md §10).

Each database kind has one schema file (four for `company`: tables, metric views, diligence
tables, diligence views) in `schema/`, holding its final tables and views: `audit` for
`shared/audit.duckdb`, `dictionary` for `shared/dictionary.duckdb`, `analytics` for
`shared/analytics.duckdb`, `company` for each `companies/<company>.duckdb`. A
database is created from its file once, when it does not exist yet. There are no migrations: to
change a schema, edit its file and re-create the data folder (docs/CUSTOMISING.md).
"""

import re
from functools import cache
from pathlib import Path

import duckdb

from fin.audit.errors import DataFolderNotWritable, DependencyVersionMismatch, SchemaFailed
from fin.db.conn import connect
from fin.paths import analytics_db_path, audit_db_path, companies_dir, dictionary_db_path

SCHEMA_DIR = Path(__file__).parent / "schema"

# The files run, in order, to create each kind of database.
SCHEMA_FILES = {
    "audit": ["audit.sql"],
    "dictionary": ["dictionary.sql"],
    "analytics": ["analytics.sql"],
    "company": ["company.sql", "company_views.sql", "diligence.sql", "diligence_views.sql"],
}


def all_databases(root: Path) -> list[Path]:
    """Every database file in the data folder that exists: the shared ones and each company's."""
    shared = [audit_db_path(root), dictionary_db_path(root), analytics_db_path(root)]
    found = shared + sorted(companies_dir(root).glob("*.duckdb"))
    return [path for path in found if path.is_file()]


# (table, column) of every table, and (table or view, NULL) of every table and view.
SHAPE = """
SELECT c.table_name, c.column_name
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
UNION SELECT table_name, NULL FROM information_schema.tables
"""
VIEW = re.compile(r"^\s*CREATE\s+VIEW\s+(\w+)", re.IGNORECASE | re.MULTILINE)


def _create(con: duckdb.DuckDBPyConnection, kind: str) -> None:
    con.execute("BEGIN TRANSACTION")
    for name in SCHEMA_FILES[kind]:
        con.execute((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    con.execute("COMMIT")


@cache
def expected_shape(kind: str) -> frozenset[tuple[str, str | None]]:
    """What SHAPE finds in a database just created from the schema files of `kind`. The views are
    only named (creating them takes a second): every statement but CREATE VIEW runs in memory."""
    views: set[tuple[str, None]] = set()
    with connect(Path(":memory:"), write=True) as con:
        for name in SCHEMA_FILES[kind]:
            for statement in con.extract_statements(
                (SCHEMA_DIR / name).read_text(encoding="utf-8")
            ):
                view = VIEW.search(statement.query)
                if view:
                    views.add((view[1], None))
                else:
                    con.execute(statement)
        return frozenset(con.execute(SHAPE).fetchall()) | views


def is_old_format(con: duckdb.DuckDBPyConnection, kind: str) -> bool:
    """True if the open database was made by an older fin: it lacks a table, a table column or a
    view its schema files create (extra ones are fine)."""
    found = set(con.execute(SHAPE).fetchall())
    return not expected_shape(kind) <= found


def old_format_error(path: Path) -> DependencyVersionMismatch:
    return DependencyVersionMismatch(
        f"{path.name} was made by an older fin; move the data folder away and re-create it with "
        "`uv run fin init`"
    )


def ensure_database(path: Path, kind: str) -> bool:
    """Create `path` from its schema file(s) if it does not exist (returns True). An existing file
    is only checked to not be from an older fin (E002). Raises E503 if creating it fails; the
    half-made file is removed. E003 when its folder cannot be made or the half-made file removed."""
    if path.exists():
        with connect(path) as con:
            if is_old_format(con, kind):
                raise old_format_error(path)
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot create {path.parent}: {exc}") from exc
    try:
        with connect(path, write=True) as con:
            _create(con, kind)
    except duckdb.Error as exc:
        remove_database(path)
        cause = " ".join(str(exc).split())
        raise SchemaFailed(f"{path.name}: creating the {kind} database failed ({cause})") from exc
    return True


def remove_database(path: Path) -> None:
    """Delete a database file and its WAL file. E003 when that fails."""
    for stale in (path, path.with_name(path.name + ".wal")):
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot remove {stale}: {exc}") from exc
