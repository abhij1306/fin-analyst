"""Contract coverage (`fin contracts coverage`, docs/fdd.md F1): do the biggest customers and
vendors have a contract on file, and what do the contracts say.

The view `contract_coverage` (db/schema/fdd_contracts.sql) ranks every customer (core revenue) and
vendor (purchases) of the last annual period; this shows the top `[fdd] coverage_top_n` of each.
`coverage` is the data function the FDD report calls.
"""

from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.config import load_config
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees


def coverage(
    con: duckdb.DuckDBPyConnection, engagement_id: int, top_n: int
) -> list[dict[str, Any]]:
    """The top_n customers and top_n vendors of the latest engagement's last period, by rank."""
    return fetch_dicts(
        con,
        "SELECT * FROM contract_coverage WHERE engagement_id = ? AND rank <= ? "
        "ORDER BY party_kind, rank",
        [engagement_id, top_n],
    )


def show(run: Run, company: str) -> list[dict[str, Any]]:
    top_n = load_config(run.root).coverage_top_n
    with company_database(run, company) as con:
        return coverage(con, require_open(con, company)["engagement_id"], top_n)


def _flag(value: bool | None, yes: str) -> str:
    return "not read" if value is None else (yes if value else "no")


def format_row(row: dict[str, Any]) -> str:
    head = f"{row['party_kind']} #{row['rank']} {row['counterparty']} {rupees(row['amount_inr'])}"
    if not row["contract_on_file"]:
        return f"{head}: no contract on file"
    if row["expired"]:
        expiry = f"ended {row['end_date']}, before the period end"
    elif row["expires_within_12m"]:
        expiry = f"ends {row['end_date']}, within 12 months of the period end"
    else:
        expiry = f"ends {row['end_date']}"
    lock = row["lock_in_left_months"]
    return (
        f"{head}: contract #{row['contract_id']} {expiry}; auto-renew "
        f"{_flag(row['auto_renew'], 'yes')}; change of control "
        f"{_flag(row['change_of_control'], 'clause')}; lock-in left "
        f"{'not read' if lock is None else f'{lock} months'} ({row['file_name']} p.{row['page']})"
    )


def format_coverage(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no customers or vendors: load the registers (`fin ingest --kind registers`)"
    period = rows[0]["period"]
    return f"contract coverage, {period} (period end {rows[0]['period_end']}):\n" + "\n".join(
        format_row(row) for row in rows
    )
