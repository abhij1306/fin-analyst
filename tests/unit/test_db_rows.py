"""fin.db.rows.fetch_one: the one row of an aggregate or INSERT ... RETURNING, never None."""

from pathlib import Path

import pytest

from fin.audit.errors import UnexpectedError
from fin.db.conn import connect
from fin.db.rows import fetch_one


def test_fetch_one_returns_the_row(tmp_path: Path) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con:
        assert fetch_one(con, "SELECT ? + 1, 'a'", [1]) == (2, "a")


def test_fetch_one_refuses_no_row_with_e012(tmp_path: Path) -> None:
    with connect(tmp_path / "x.duckdb", write=True) as con:
        con.execute("CREATE TABLE t (a INTEGER)")

        with pytest.raises(UnexpectedError) as caught:
            fetch_one(con, "SELECT a FROM t")

    assert caught.value.code == "E012"
