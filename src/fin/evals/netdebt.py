"""Golden checks for F4 (docs/fdd.md): net debt at a period end.

`net_debt`: one figure of the view `net_debt` for the period (`ledger`: cash, debt, debt_like or
net_debt): the cash, debt and debt-like ledgers' closing balances in the period's trial balance
(cash as debit less credit, debt and debt-like as credit less debit), debt-like including the
accepted items, and net debt = debt + debt-like - cash. A person checks cash and debt against the
balance sheet and the trial balance.
"""

from collections.abc import Callable
from typing import Any

import duckdb

COMPONENTS = ("cash", "debt", "debt_like", "net_debt")
KINDS = ("net_debt",)
NEEDS = {"net_debt": ("period", "ledger")}
LEDGERS = {"net_debt": COMPONENTS}
FIN_VALUE = {
    "net_debt": (
        "SELECT CASE ? WHEN 'cash' THEN cash WHEN 'debt' THEN debt WHEN 'debt_like' THEN debt_like "
        "WHEN 'net_debt' THEN net_debt END FROM net_debt WHERE period = ?",
        ("ledger", "period"),
    ),
}
_PERIODS = (
    "SELECT period, trial_balance_file FROM net_debt WHERE trial_balance_file IS NOT NULL "
    "ORDER BY n, period"
)


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, str]]:
    """Every F4 figure fin has, with its source; `row` makes a golden line."""
    rows: list[dict[str, Any]] = []
    for period, file_name in con.execute(_PERIODS).fetchall():
        source = f"{file_name} (ledgers by net debt class; net_debt_lines)"
        rows += [row("net_debt", period, source, ledger=part, unit="rupees") for part in COMPONENTS]
    return rows
