"""`fin sql --write` refuses, before any statement runs, statements that write to a diligence table
(E806) or that would end its transaction or reach past the company database (E504)."""

from pathlib import Path

import pytest

from fin.audit.errors import DiligenceTableWrite, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.query.write import check_write_sql, write_sql


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        ("INSERT INTO gate_approvals SELECT * FROM gate_approvals", "gate_approvals"),
        ("UPDATE adjustments SET status = 'accepted'", "adjustments"),
        ('DELETE FROM main."Accountant_Queries"', "accountant_queries"),
        ("UPDATE facts SET value = 1; UPDATE engagement SET status = 'closed'", "engagement"),
        ("DROP TABLE gate_approvals", "gate_approvals"),
        ("UPDATE facts SET value = 1 WHERE doc_id IN (SELECT 1 FROM adjustments)", "adjustments"),
    ],
)
def test_writes_naming_a_diligence_table_are_refused(tmp_path: Path, sql: str, found: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(DiligenceTableWrite) as e:
        check_write_sql(con, sql)

    assert str(e.value) == (
        f"E806: `fin sql --write` does not change the diligence tables (found: {found}); use "
        "`fin engagement`, `fin query`, `fin adjust` or `fin gate`. Nothing was run"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE facts SET value = 1 WHERE engagement_id = 2",
        "SELECT * FROM gate_approvals; UPDATE facts SET value = 1",
        "DELETE FROM facts WHERE account = 'adjusted_ebitda'",
    ],
)
def test_other_writes_pass(tmp_path: Path, sql: str) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con:
        assert check_write_sql(con, sql) is None


def test_sql_that_does_not_parse_is_e504(tmp_path: Path) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(SqlFailed) as e:
        check_write_sql(con, "UPDATE facts SET")

    assert str(e.value).startswith("E504: cannot parse the SQL: Parser Error")


@pytest.mark.parametrize(
    ("sql", "refused"),
    [
        ("UPDATE facts SET value = 1; COMMIT", "BEGIN, COMMIT and ROLLBACK"),
        ("ROLLBACK", "BEGIN, COMMIT and ROLLBACK"),
        ("ATTACH 'other.duckdb' AS other", "ATTACH"),
        ("DETACH other", "DETACH"),
        ("LOAD httpfs", "LOAD and INSTALL"),
    ],
)
def test_statements_leaving_the_transaction_are_refused(
    tmp_path: Path, sql: str, refused: str
) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(SqlFailed) as e:
        check_write_sql(con, sql)

    assert str(e.value) == (
        "E504: `fin sql --write` runs the SQL in its own transaction on the company database; "
        f"{refused} are not allowed. Nothing was run"
    )


def test_a_commit_in_the_batch_runs_nothing(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    run = Run("sql", {}, tmp_path)

    with pytest.raises(SqlFailed):
        write_sql(run, "acme", "CREATE TABLE early (x INT); COMMIT; SELECT * FROM missing")

    with connect(path) as con:
        assert con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'early'"
        ).fetchone() == (0,)
    assert not (tmp_path / "backups").exists()
