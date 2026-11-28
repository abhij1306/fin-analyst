"""Period-end revenue (SPEC-20): the share of a period's sales invoiced at its very end.

Per period of the latest engagement, the sales register revenue in the period's last month and in
its last `period_end_days` days (the forensic setting), and each as a share of the period's
revenue: the SQL macro `period_end_revenue(days)` in db/schema/diligence_analytics.sql. A
measure, not a test: nothing is flagged. `fin analytics` and the Forensic screen both show it
through `rows` and `lines`.
"""

from pathlib import Path
from typing import Any

import duckdb

from fin.db.rows import fetch_dicts
from fin.diligence.engagement import rupees
from fin.forensic import settings


def rows(con: duckdb.DuckDBPyConnection, root: Path, company: str) -> list[dict[str, Any]]:
    """One row per engagement period with sales register lines. E990 for an invalid [forensic]
    table."""
    days = settings.resolve(root, company)["period_end_days"]
    return fetch_dicts(con, "SELECT * FROM period_end_revenue(?)", [days])


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value:.2%}"


def lines(found: list[dict[str, Any]]) -> list[str]:
    """Each period in words (sales register, Amount in Rs before GST)."""
    return [
        f"{r['period']} ({r['basis']}, ends {r['period_end']}): revenue {rupees(r['revenue'])}; "
        f"last month {rupees(r['last_month_revenue'])} ({_pct(r['last_month_share'])}); last "
        f"{r['last_days']} days {rupees(r['last_days_revenue'])} ({_pct(r['last_days_share'])})"
        for r in found
    ]
