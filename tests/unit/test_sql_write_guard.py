"""`fin sql --write` refuses statements that write to a diligence table (E806), before any runs."""

from pathlib import Path

import pytest

from fin.audit.errors import DiligenceTableWrite, SqlFailed
from fin.db.conn import connect
from fin.query.write import check_not_diligence


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
        check_not_diligence(con, sql)

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
        assert check_not_diligence(con, sql) is None


def test_sql_that_does_not_parse_is_e504(tmp_path: Path) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con, pytest.raises(SqlFailed) as e:
        check_not_diligence(con, "UPDATE facts SET")

    assert str(e.value).startswith("E504: cannot parse the SQL: Parser Error")
