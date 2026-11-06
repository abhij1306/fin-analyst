"""`fin migrate`: numbered SQL migrations for shared and company files, backup first, restore on
failure."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.db.migrate import current_version

runner = CliRunner()

Query = Callable[..., list[tuple[Any, ...]]]


def run_id_of(stderr: str) -> str:
    match = re.search(r", run_id (\S+)$", stderr, re.MULTILINE)
    assert match is not None, stderr
    return match.group(1)


def without_company_migrations(migrations_dir: Path) -> Path:
    """These tests write their own company migrations, so start from none."""
    folder = migrations_dir / "company"
    for path in folder.glob("*.sql"):
        path.unlink()
    return folder


def make_company(root: Path, name: str = "acme") -> Path:
    """A company file that predates its migrations: one table with one known row."""
    path = root / "companies" / f"{name}.duckdb"
    with connect(path, write=True) as con:
        con.execute("CREATE TABLE seed (v DECIMAL(20,2))")
        con.execute("INSERT INTO seed VALUES (1234.50)")
    return path


def query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(path) as con:
        return con.execute(sql).fetchall()


def tables(path: Path) -> list[str]:
    rows = query(path, "SELECT table_name FROM information_schema.tables ORDER BY table_name")
    return [row[0] for row in rows]


def test_migrate_backs_up_first_applies_and_is_idempotent(
    data_root: Path, migrations_dir: Path, audit_query: Query
) -> None:
    without_company_migrations(migrations_dir)
    runner.invoke(app, ["init"])
    company = make_company(data_root)
    (migrations_dir / "audit" / "003_probe.sql").write_text(
        "CREATE TABLE probe (x INTEGER);\nINSERT INTO probe VALUES (42);\n", encoding="utf-8"
    )
    (migrations_dir / "company" / "001_facts.sql").write_text(
        "CREATE TABLE facts (value DECIMAL(20,2));\n", encoding="utf-8"
    )
    (migrations_dir / "company" / "002_facts_scope.sql").write_text(
        "ALTER TABLE facts ADD COLUMN scope VARCHAR;\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 0, result.stderr
    run_id = run_id_of(result.stderr)
    backup = data_root / "backups" / run_id
    assert result.stdout.splitlines() == [
        f"shared/audit.duckdb: schema version 2 -> 3 "
        f"(backup: backups/{run_id}/shared/audit.duckdb)",
        "shared/dictionary.duckdb: up to date at schema version 1",
        "shared/analytics.duckdb: up to date at schema version 1",
        f"companies/acme.duckdb: schema version 0 -> 2 "
        f"(backup: backups/{run_id}/companies/acme.duckdb)",
    ]
    # Backups hold the files as they were before migrating.
    assert current_version(backup / "shared" / "audit.duckdb") == 2
    assert tables(backup / "companies" / "acme.duckdb") == ["seed"]
    assert query(backup / "companies" / "acme.duckdb", "SELECT v FROM seed")[0][0] == 1234.5
    # Live files are migrated, data kept.
    assert current_version(data_root / "shared" / "audit.duckdb") == 3
    assert audit_query("SELECT x FROM probe") == [(42,)]
    assert tables(company) == ["facts", "schema_version", "seed"]
    assert query(company, "SELECT version, name, run_id FROM schema_version ORDER BY 1") == [
        (1, "001_facts", run_id),
        (2, "002_facts_scope", run_id),
    ]
    assert audit_query(
        "SELECT db_file, from_version, to_version, backup_path, status, error_code "
        "FROM migrations WHERE run_id = ? ORDER BY db_file",
        [run_id],
    ) == [
        ("companies/acme.duckdb", 0, 2, f"backups/{run_id}/companies/acme.duckdb", "applied", None),
        ("shared/audit.duckdb", 2, 3, f"backups/{run_id}/shared/audit.duckdb", "applied", None),
    ]

    again = runner.invoke(app, ["migrate"])

    assert again.exit_code == 0
    assert again.stdout.splitlines() == [
        "shared/audit.duckdb: up to date at schema version 3",
        "shared/dictionary.duckdb: up to date at schema version 1",
        "shared/analytics.duckdb: up to date at schema version 1",
        "companies/acme.duckdb: up to date at schema version 2",
    ]
    assert [p.name for p in (data_root / "backups").iterdir()] == [run_id]
    assert audit_query(
        "SELECT count(*) FROM migrations WHERE run_id = ?", [run_id_of(again.stderr)]
    ) == [(0,)]


def test_failed_migration_restores_the_backup_and_is_e503(
    data_root: Path, migrations_dir: Path, audit_query: Query
) -> None:
    without_company_migrations(migrations_dir)
    runner.invoke(app, ["init"])
    company = make_company(data_root)
    (migrations_dir / "company" / "001_good.sql").write_text(
        "CREATE TABLE facts (value DECIMAL(20,2));\n", encoding="utf-8"
    )
    (migrations_dir / "company" / "002_bad.sql").write_text(
        "CREATE TABLE half_done (x INTEGER);\nSELECT * FROM no_such_table;\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 2
    run_id = run_id_of(result.stderr)
    first_line = result.stderr.splitlines()[0]
    assert first_line.startswith("E503: companies/acme.duckdb: migration 002_bad failed (")
    assert first_line.endswith(f"restored from backups/{run_id}/companies/acme.duckdb")
    # Restored to the state before `fin migrate`: 001 was committed but is undone too.
    assert current_version(company) == 0
    assert tables(company) == ["seed"]
    assert query(company, "SELECT v FROM seed")[0][0] == 1234.5
    assert audit_query(
        "SELECT from_version, to_version, status, error_code FROM migrations WHERE run_id = ?",
        [run_id],
    ) == [(0, 2, "failed", "E503")]
    assert audit_query(
        "SELECT error_code FROM events WHERE run_id = ? AND level = 'error'", [run_id]
    ) == [("E503",)]


def test_failed_migration_of_a_new_file_removes_it(data_root: Path, migrations_dir: Path) -> None:
    (migrations_dir / "audit" / "001_runs_events_migrations.sql").write_text(
        "CREATE TABLE runs (x INTEGER);\nSELECT * FROM no_such_table;\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 2
    assert result.stderr.splitlines()[0].startswith(
        "E503: shared/audit.duckdb: migration 001_runs_events_migrations failed ("
    )
    assert result.stderr.splitlines()[0].endswith("new file removed")
    assert not (data_root / "shared" / "audit.duckdb").exists()
    # Not recorded in the database (there is none), but still in the JSONL log.
    run_id = run_id_of(result.stderr)
    [log] = (data_root / "logs").glob(f"*/{run_id}.jsonl")
    assert '"error_code": "E503"' in log.read_text(encoding="utf-8")


def test_migration_numbering_gap_is_e503(data_root: Path, migrations_dir: Path) -> None:
    without_company_migrations(migrations_dir)
    runner.invoke(app, ["init"])
    make_company(data_root)
    (migrations_dir / "company" / "001_a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (migrations_dir / "company" / "003_c.sql").write_text("SELECT 1;\n", encoding="utf-8")

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 2
    assert result.stderr.splitlines()[0] == (
        f"E503: 003_c.sql: migrations in {migrations_dir / 'company'} must be named NNN_name.sql "
        "and numbered 001, 002, ... with no gaps (expected 002)"
    )


def test_migrate_before_init_is_e003(data_root: Path) -> None:
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 2
    assert result.stderr.splitlines()[0] == (
        f"E003: {data_root} is not set up; run `uv run fin init`"
    )
