"""Query results as plain dicts (column name -> value), ready for JSON."""

from typing import Any

import duckdb

from fin.audit.errors import UnexpectedError


def fetch_dicts(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run `sql` with `params` (positional, or named for $name) and return one dict per row."""
    result = con.execute(sql, params or [])
    names = [column[0] for column in result.description or []]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def fetch_one(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> tuple[Any, ...]:
    """The row of `sql` that always gives one (an aggregate, or INSERT ... RETURNING). E012 when
    it gives none: a defect in fin, never read as an empty value."""
    row = con.execute(sql, params or []).fetchone()
    if row is None:
        raise UnexpectedError(f"no row from: {' '.join(sql.split())[:120]}")
    return row
