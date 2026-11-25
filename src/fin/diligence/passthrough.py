"""`fin engagement passthrough`: the user's list of pass-through customers for the open engagement.

Pass-through customers are the SI/reseller customers (tokens, as in the sales register) whose deals
pass through at roughly zero margin. The user supplies the list; fin only records it (table
`passthrough_customers`, db/schema/diligence.sql) and shows what it does to revenue: the views
`revenue_split` and `passthrough_lines` (db/schema/diligence_split_views.sql) split each period's
sales into core and pass-through (project) revenue and link each pass-through line to its OEM
purchase. Setting the list again replaces it; each setting is appended to `diligence_events` with
the engagement. Claude proposes the normalisation adjustment that strips pass-through revenue and
its cost (`fin adjust propose`); this module changes no figure.
"""

import json
from typing import Any

import duckdb

from fin.audit.errors import PassthroughInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees

LOADED_SALES = (
    "SELECT r.customer, r.amount_inr FROM register_lines r JOIN source_files f "
    "ON f.file_id = r.file_id AND f.status = 'loaded' AND f.engagement_id = ? "
    "WHERE r.kind = 'sales'"
)


def _check_customers(
    con: duckdb.DuckDBPyConnection, engagement_id: int, company: str, customers: list[str]
) -> None:
    """E845 unless every token is on a loaded sales register line."""
    known = {row[0] for row in con.execute(LOADED_SALES, [engagement_id]).fetchall()}
    if not known:
        raise PassthroughInvalid(
            f"{company} has no loaded sales register for the open engagement; nothing changed. "
            f"Load it first: `fin ingest {company} <file> --kind registers`"
        )
    unknown = [c for c in customers if c not in known]
    if unknown:
        raise PassthroughInvalid(
            f"not a customer of {company}'s sales register: {', '.join(unknown)}; nothing "
            "changed. Customers are tokens such as CUST-0001 (`fin sql`: SELECT DISTINCT customer "
            "FROM register_lines WHERE kind = 'sales')"
        )


def set_customers(
    run: Run, company: str, customers: list[str], source: str | None
) -> dict[str, Any]:
    """Replace the open engagement's list with `customers` (none clears it). E845 for a token not
    on a loaded sales register."""
    customers = list(dict.fromkeys(customers))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        if customers:
            _check_customers(con, engagement_id, company, customers)
        con.execute("DELETE FROM passthrough_customers WHERE engagement_id = ?", [engagement_id])
        now = utcnow()
        for customer in customers:
            con.execute(
                "INSERT INTO passthrough_customers VALUES (?, ?, ?, ?, ?)",
                [engagement_id, customer, source, run.run_id, now],
            )
        run.record(
            "diligence_events",
            {
                "company": company,
                "engagement_id": engagement_id,
                "kind": "engagement",
                "item_id": str(engagement_id),
                "status": engagement["status"],
                "detail": json.dumps(
                    {"passthrough_customers": customers, "source": source}, default=str
                ),
            },
        )
    return show(run, company)


def show(run: Run, company: str) -> dict[str, Any]:
    """The open engagement's list with each customer's sales lines and revenue, and the
    `revenue_split` rows."""
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        customers = fetch_dicts(
            con,
            "SELECT p.customer, p.source, count(s.customer) AS sales_lines, "
            "sum(s.amount_inr) AS revenue FROM passthrough_customers p "
            f"LEFT JOIN ({LOADED_SALES}) s ON s.customer = p.customer "
            "WHERE p.engagement_id = ? GROUP BY p.customer, p.source ORDER BY p.customer",
            [engagement["engagement_id"], engagement["engagement_id"]],
        )
        split = fetch_dicts(con, "SELECT * FROM revenue_split ORDER BY basis, period")
    return {"company": company, "engagement": engagement, "customers": customers, "split": split}


def format_show(result: dict[str, Any]) -> str:
    out = [
        f"{result['company']}: pass-through customers of engagement "
        f"#{result['engagement']['engagement_id']}: {len(result['customers']) or 'none'}"
    ]
    for c in result["customers"]:
        out.append(
            f"  {c['customer']}: {c['sales_lines']} sales lines, {rupees(c['revenue'])} "
            f"(source: {c['source']})"
        )
    out.append("revenue split (sales register, Amount in Rs before GST; view revenue_split):")
    for row in result["split"]:
        label = row["period"] + ("" if row["basis"] == "audited" else " (unaudited)")
        if row["sales_revenue"] is None:
            out.append(f"  {label}: no sales register lines")
            continue
        growth = "-" if row["core_revenue_growth"] is None else f"{row['core_revenue_growth']:.2%}"
        out.append(
            f"  {label}: core {rupees(row['core_revenue'])}, pass-through "
            f"{rupees(row['passthrough_revenue'])} "
            f"(licence {rupees(row['passthrough_licence_revenue'])}, "
            f"services {rupees(row['passthrough_services_revenue'])}), linked purchase cost "
            f"{rupees(row['linked_purchase_cost'])}, core growth {growth}"
        )
    return "\n".join(out)
