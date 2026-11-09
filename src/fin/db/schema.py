"""Creating the databases from their schema files (PLAN.md §10).

Each database kind has one schema file (three for `company`: tables, metric views, diligence
tables) in `schema/`, holding its final tables and views: `audit` for `shared/audit.duckdb`,
`dictionary` for `shared/dictionary.duckdb`, `analytics` for `shared/analytics.duckdb`, `company`
for each `companies/<company>.duckdb`. A
database is created from its file once, when it does not exist yet. There are no migrations: to
change a schema, edit its file and re-create the data folder (docs/CUSTOMISING.md).
"""

from pathlib import Path

import duckdb

from fin.audit.errors import DependencyVersionMismatch, SchemaFailed
from fin.db.conn import connect
from fin.paths import analytics_db_path, audit_db_path, companies_dir, dictionary_db_path

SCHEMA_DIR = Path(__file__).parent / "schema"

# The files run, in order, to create each kind of database.
SCHEMA_FILES = {
    "audit": ["audit.sql"],
    "dictionary": ["dictionary.sql"],
    "analytics": ["analytics.sql"],
    "company": ["company.sql", "company_views.sql", "diligence.sql"],
}


def all_databases(root: Path) -> list[Path]:
    """Every database file in the data folder that exists: the shared ones and each company's."""
    shared = [audit_db_path(root), dictionary_db_path(root), analytics_db_path(root)]
    found = shared + sorted(companies_dir(root).glob("*.duckdb"))
    return [path for path in found if path.is_file()]


def is_old_format(con: duckdb.DuckDBPyConnection) -> bool:
    """True if the open database was made by an older fin (one with schema migrations)."""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'schema_version'"
    ).fetchone()
    return row is not None and row[0] > 0


def old_format_error(path: Path) -> DependencyVersionMismatch:
    return DependencyVersionMismatch(
        f"{path.name} was made by an older fin; move the data folder away and re-create it with "
        "`uv run fin init`"
    )


def ensure_database(path: Path, kind: str) -> bool:
    """Create `path` from its schema file(s) if it does not exist (returns True). An existing file
    is only checked to not be from an older fin. Raises E503 if creating it fails; the half-made
    file is removed."""
    if path.exists():
        with connect(path) as con:
            if is_old_format(con):
                raise old_format_error(path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with connect(path, write=True) as con:
            con.execute("BEGIN TRANSACTION")
            for name in SCHEMA_FILES[kind]:
                con.execute((SCHEMA_DIR / name).read_text(encoding="utf-8"))
            con.execute("COMMIT")
    except duckdb.Error as exc:
        for stale in (path, path.with_name(path.name + ".wal")):
            stale.unlink(missing_ok=True)
        cause = " ".join(str(exc).split())
        raise SchemaFailed(f"{path.name}: creating the {kind} database failed ({cause})") from exc
    return True
