"""Golden checks for the orderbook and revenue visibility (docs/fdd.md F7), in rupees.

`ledger` is the backlog source (`orderbook`, `billed_not_earned` or `contract`), `period` the last
annual period of the latest engagement (the year after it is the visibility year):

- `backlog_remaining`: what is still to come from the source
  (`revenue_visibility.remaining_amount`);
- `visibility_covered`: the part of it in the 12 months after the period end
  (`revenue_visibility.covered_next_year`).

The source a person checks is the orderbook rows (file + sheet!row), the register lines or the
contract (file + page), spread straight-line over their remaining term.
"""

from collections.abc import Callable

import duckdb

KINDS = ("backlog_remaining", "visibility_covered")
NEEDS = {kind: ("period", "ledger") for kind in KINDS}
_LATEST = "(SELECT max(engagement_id) FROM engagement)"
FIN_VALUE = {
    kind: (
        f"SELECT {column} FROM revenue_visibility WHERE period = ? AND source = ? "
        f"AND engagement_id = {_LATEST} AND items > 0",
        ("period", "ledger"),
    )
    for kind, column in (
        ("backlog_remaining", "remaining_amount"),
        ("visibility_covered", "covered_next_year"),
    )
}
_SOURCES = f"""
SELECT period, source FROM revenue_visibility WHERE items > 0 AND engagement_id = {_LATEST}
ORDER BY source
"""


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, str]]:
    """Every backlog source with items, for both figures, with where a person checks it."""
    return [
        row(kind, period, f"{source}: its items in the backlog lines", ledger=source, unit="rupees")
        for period, source in con.execute(_SOURCES).fetchall()
        for kind in KINDS
    ]
