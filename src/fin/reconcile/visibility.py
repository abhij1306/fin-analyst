"""Contracted backlog and revenue visibility (`fin visibility`, docs/fdd.md F7).

The views `backlog_lines` and `revenue_visibility` (db/schema/fdd_orderbook.sql) give, per backlog
source (the unbilled orderbook, revenue billed but not yet earned at the last period end, customer
contract value remaining), what is still to come and the part that falls in the 12 months after
the last period end, against that period's core revenue. `visibility` is the data function the FDD
report calls; the sources are never added together (an order can also be in a contract).
"""

from typing import Any

import duckdb

from fin.audit.errors import VisibilityUnavailable
from fin.audit.logger import Run
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees

SOURCES = {
    "orderbook": "unbilled orderbook",
    "billed_not_earned": "billed, not yet earned",
    "contract": "customer contracts remaining",
}
MISSING = {
    "orderbook": "no orderbook loaded (`fin ingest <co> <file> --kind orderbook`)",
    "billed_not_earned": "no sales register line billed up to the period end with a service "
    "period running past it",
    "contract": "no customer contract running past the period end (`fin recast contracts`)",
}


def visibility(con: duckdb.DuckDBPyConnection, engagement_id: int) -> dict[str, Any]:
    """{"sources": the revenue_visibility rows, "notes": [{source, note, items}], "orderbook":
    {as_at, file_name, period_end, at_period_end} of the orderbook used, or None} for the
    engagement.
    E961 when no source has an item."""
    rows = fetch_dicts(
        con, "SELECT * FROM revenue_visibility WHERE engagement_id = ?", [engagement_id]
    )
    if not rows or not any(row["items"] for row in rows):
        raise VisibilityUnavailable(
            "no backlog source is loaded: " + "; ".join(MISSING.values()) + ". Nothing shown"
        )
    notes = fetch_dicts(
        con,
        "SELECT source, note, count(*) AS items FROM backlog_lines WHERE note IS NOT NULL "
        "GROUP BY ALL ORDER BY ALL",
    )
    orderbook = fetch_dicts(
        con,
        "SELECT DISTINCT o.as_at, o.file_name, b.period_end, "
        "o.as_at = b.period_end AS at_period_end "
        "FROM orderbook_current o JOIN visibility_basis b USING (engagement_id) "
        "WHERE o.engagement_id = ?",
        [engagement_id],
    )
    return {"sources": rows, "notes": notes, "orderbook": orderbook[0] if orderbook else None}


def orderbook_date_note(data: dict[str, Any]) -> str | None:
    """The as-at line of the orderbook used, with a warning when it is not the period end."""
    found = data.get("orderbook")
    if found is None:
        return None
    text = f"orderbook as at {found['as_at']} ({found['file_name']})"
    if not found["at_period_end"]:
        text += (
            f": not the period end {found['period_end']}; its unbilled value is what was still "
            f"to come on {found['as_at']}, and the spread starts the day after it"
        )
    return text


def show(run: Run, company: str) -> dict[str, Any]:
    with company_database(run, company) as con:
        return visibility(con, require_open(con, company)["engagement_id"])


def format_visibility(data: dict[str, Any]) -> str:
    rows = data["sources"]
    head = rows[0]
    core = head["core_revenue"]
    out = [
        f"revenue visibility, {head['year_start']} to {head['year_end']} (the 12 months after "
        f"{head['period']}, which ended {head['period_end']}); {head['period']} core revenue "
        f"{'not loaded' if core is None else rupees(core)}"
    ]
    for row in rows:
        name = SOURCES[row["source"]]
        if not row["items"]:
            out.append(f"  {name}: not loaded: {MISSING[row['source']]}")
            continue
        pct = "" if row["covered_pct"] is None else f" = {row['covered_pct']}% of core revenue"
        unusable = f", {row['unusable_items']} not counted in full" if row["unusable_items"] else ""
        out.append(
            f"  {name}: {row['items']} items, still to come {rupees(row['remaining_amount'])}, "
            f"in the next 12 months {rupees(row['covered_next_year'])}{pct}{unusable}"
        )
    if (dated := orderbook_date_note(data)) is not None:
        out.append(f"  {dated}")
    out += [
        f"  note: {n['items']} {SOURCES[n['source']]} items: {n['note']}" for n in data["notes"]
    ]
    out.append(
        "the sources overlap (an order can be in a contract too): they are not added together"
    )
    return "\n".join(out)
