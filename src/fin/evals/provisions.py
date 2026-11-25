"""Golden checks for F3 (docs/fdd.md): receivables ageing and the provision balances.

`receivable_ageing`: the open sales invoices at a year end in one ageing bucket (`ledger`: 0-90,
91-180, 181-365, over 365 or total), Amount in Rs, as the view `receivable_provision` gives them;
a person checks them against the sales register and the bank statements. `doubtful_debt_provision`
and `gratuity_provision`: the confirmed provision ledgers' balance (credit less debit) in the
period's trial balance (`provision_balances`). The indicative gratuity is not a golden kind: it is
computed on a config.toml assumption, not printed anywhere (docs/decisions/fdd-f3.md).
"""

from collections.abc import Callable
from typing import Any

import duckdb

BUCKETS = ("0-90", "91-180", "181-365", "over 365", "total")
KINDS = ("receivable_ageing", "doubtful_debt_provision", "gratuity_provision")
NEEDS = {
    "receivable_ageing": ("period", "ledger"),
    "doubtful_debt_provision": ("period",),
    "gratuity_provision": ("period",),
}
LEDGERS = {"receivable_ageing": BUCKETS}
_BALANCE = "SELECT provision_inr FROM provision_balances WHERE period = ? AND kind = '{kind}'"
FIN_VALUE = {
    "receivable_ageing": (
        "SELECT open_inr FROM receivable_provision WHERE period = ? AND bucket = ?",
        ("period", "ledger"),
    ),
    "doubtful_debt_provision": (_BALANCE.format(kind="doubtful_debts"), ("period",)),
    "gratuity_provision": (_BALANCE.format(kind="gratuity"), ("period",)),
}
_AGEING = "SELECT DISTINCT period, year_end FROM receivable_provision ORDER BY period"
_BALANCES = (
    "SELECT period, kind, trial_balance_file FROM provision_balances "
    "WHERE provision_inr IS NOT NULL ORDER BY period, kind"
)


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, str]]:
    """Every F3 figure fin has, with its source; `row` makes a golden line."""
    rows: list[dict[str, Any]] = []
    for period, year_end in con.execute(_AGEING).fetchall():
        source = f"sales register and bank statements: invoices open at {year_end}"
        rows += [
            row("receivable_ageing", period, source, ledger=bucket, unit="rupees")
            for bucket in BUCKETS
        ]
    for period, kind, file_name in con.execute(_BALANCES).fetchall():
        name = "doubtful_debt_provision" if kind == "doubtful_debts" else "gratuity_provision"
        rows.append(row(name, period, f"{file_name} (confirmed {kind} ledgers)", unit="rupees"))
    return rows
