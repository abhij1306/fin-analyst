"""Golden checks for the working capital outputs (D7): `nwc_year_end` and `nwc_monthly`.

Both are in rupees, as the views of db/schema/diligence_nwc.sql give them, and name the line in
`ledger`: a component or `net_working_capital`. `nwc_year_end`: period is the period (FY25, or the
stub). `nwc_monthly`: period is the month end (YYYY-MM-DD). The source a person checks is the trial
balance (year-end), or the Ledger Vouchers on the previous trial balance's closing (monthly).
"""

from collections.abc import Callable
from typing import Any

import duckdb

KINDS = ("nwc_year_end", "nwc_monthly")
COMPONENTS = (
    "net_working_capital",
    "trade_receivables",
    "inventories",
    "other_current_assets",
    "trade_payables",
    "other_current_liabilities",
)
_CASE = "CASE ? " + " ".join(f"WHEN '{c}' THEN {c}" for c in COMPONENTS) + " END"
FIN_VALUE = {
    "nwc_year_end": (
        f"SELECT {_CASE} FROM nwc_year_end WHERE period = ?",
        ("ledger", "period"),
    ),
    "nwc_monthly": (
        f"SELECT {_CASE} FROM nwc_monthly WHERE month_end::VARCHAR = ?",
        ("ledger", "period"),
    ),
}
_YEAR_END = "SELECT period, trial_balance_file FROM nwc_year_end ORDER BY period_end"
_MONTHLY = """
SELECT m.month_end::VARCHAR, any_value(l.ledger_vouchers_file), any_value(y.trial_balance_file)
FROM nwc_monthly m
JOIN nwc_monthly_ledgers l ON l.period = m.period AND l.status = 'included'
LEFT JOIN nwc_year_end y ON y.period = l.prior_period
GROUP BY m.month_end ORDER BY 1
"""


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, str]]:
    """Every working capital line fin has, with its source; `row` makes a golden line."""
    rows: list[dict[str, Any]] = []
    for period, file_name in con.execute(_YEAR_END).fetchall():
        rows += [
            row("nwc_year_end", period, file_name, ledger=component, unit="rupees")
            for component in COMPONENTS
        ]
    for month_end, vouchers, trial_balance in con.execute(_MONTHLY).fetchall():
        source = f"{vouchers} on the closing of {trial_balance}"
        rows += [
            row("nwc_monthly", month_end, source, ledger=component, unit="rupees")
            for component in COMPONENTS
        ]
    return rows
