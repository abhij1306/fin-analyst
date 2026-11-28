"""Query results as plain dicts (column name -> value), ready for JSON."""

from typing import Any

import duckdb


def fetch_dicts(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run `sql` with `params` (positional, or named for $name) and return one dict per row."""
    result = con.execute(sql, params or [])
    names = [column[0] for column in result.description or []]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
