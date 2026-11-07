from pathlib import Path

from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import audit_db_path, dictionary_db_path

COMPANY_VIEWS = {
    "document_facts",
    "current_facts",
    "metric_definitions",
    "metric_input_accounts",
    "metrics",
    "metric_inputs",
}


def _names(path: Path, table_type: str) -> set[str]:
    with connect(path) as con:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type = ?", [table_type]
        ).fetchall()
    return {row[0] for row in rows}


def test_company_database_has_its_tables_and_metric_views(tmp_path: Path) -> None:
    path = tmp_path / "acme.duckdb"
    assert ensure_database(path, "company") is True
    assert _names(path, "BASE TABLE") == {"documents", "raw_values", "facts", "notes", "findings"}
    assert _names(path, "VIEW") == COMPANY_VIEWS


def test_existing_database_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "audit.duckdb"
    assert ensure_database(path, "audit") is True
    with connect(path, write=True) as con:
        con.execute("INSERT INTO deletions VALUES ('r', now(), 'acme', '[]', NULL, 'b')")
    assert ensure_database(path, "audit") is False
    with connect(path) as con:
        assert con.execute("SELECT count(*) FROM deletions").fetchone() == (1,)


def test_init_creates_the_shared_databases(data_root: Path) -> None:
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    assert _names(audit_db_path(data_root), "BASE TABLE") == {
        "runs", "events", "mapping_decisions", "validation_results", "queries", "reports",
        "deletions", "findings",
    }  # fmt: skip
    assert _names(dictionary_db_path(data_root), "BASE TABLE") == {"accounts", "label_map"}
