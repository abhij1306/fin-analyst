from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.audit.errors import DataFolderNotWritable, DependencyVersionMismatch
from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database, remove_database
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


def test_existing_database_missing_a_column_is_e002(tmp_path: Path) -> None:
    path = tmp_path / "audit.duckdb"
    ensure_database(path, "audit")
    with connect(path, write=True) as con:
        con.execute("ALTER TABLE deletions DROP COLUMN row_counts")

    with pytest.raises(DependencyVersionMismatch) as e:
        ensure_database(path, "audit")

    assert str(e.value) == (
        "E002: audit.duckdb was made by an older fin; move the data folder away and re-create it "
        "with `uv run fin init`"
    )


def test_existing_database_missing_a_view_or_a_view_column_is_e002(tmp_path: Path) -> None:
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute("DROP VIEW ebitda_to_cash")

    with pytest.raises(DependencyVersionMismatch):
        ensure_database(path, "company")
    # The view of an older fin, with other columns.
    with connect(path, write=True) as con:
        con.execute("CREATE VIEW ebitda_to_cash AS SELECT 1 AS x")
    with pytest.raises(DependencyVersionMismatch):
        ensure_database(path, "company")


def test_existing_database_with_an_extra_table_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "audit.duckdb"
    ensure_database(path, "audit")
    with connect(path, write=True) as con:
        con.execute("CREATE TABLE notes (x INT)")

    assert ensure_database(path, "audit") is False


def test_folder_that_cannot_be_made_is_e003(tmp_path: Path) -> None:
    (tmp_path / "shared").write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(DataFolderNotWritable) as e:
        ensure_database(tmp_path / "shared" / "audit.duckdb", "audit")

    assert str(e.value).startswith(f"E003: cannot create {tmp_path / 'shared'}: ")


def test_database_that_cannot_be_removed_is_e003(tmp_path: Path) -> None:
    path = tmp_path / "analytics.duckdb"
    path.mkdir()  # a folder where the file should be: unlink fails

    with pytest.raises(DataFolderNotWritable) as e:
        remove_database(path)

    assert str(e.value).startswith(f"E003: cannot remove {path}: ")
