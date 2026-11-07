from pathlib import Path

from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import audit_db_path, dictionary_db_path

PERIOD_COLUMNS = {"fiscal_year", "period", "period_months", "scope", "period_end"}


def test_company_database_is_created_and_its_metrics_are_queryable(tmp_path: Path) -> None:
    path = tmp_path / "acme.duckdb"
    assert ensure_database(path, "company") is True
    with connect(path) as con:
        assert con.execute("SELECT count(*) FROM metrics").fetchone() == (0,)
        assert con.execute("SELECT count(*) FROM metric_inputs").fetchone() == (0,)


def test_every_metric_has_a_definition_and_inputs(tmp_path: Path) -> None:
    """Every metric formula (a metrics_wide column) has a metric_definitions row and at least one
    metric_input_accounts row, and every definition has a formula."""
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path) as con:

        def first_column(sql: str) -> set[str]:
            return {row[0] for row in con.execute(sql).fetchall()}

        formulas = first_column("DESCRIBE metrics_wide")
        defined = first_column("SELECT metric FROM metric_definitions")
        with_inputs = first_column("SELECT metric FROM metric_input_accounts")
    assert len(defined) == 66
    assert formulas - PERIOD_COLUMNS == defined
    assert with_inputs == defined


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
    assert audit_db_path(data_root).is_file()
    assert dictionary_db_path(data_root).is_file()
