"""`fin analytics <company>`: customer, product, sector, pricing and retention analytics (D9).

Everything is SQL on the views in db/schema/diligence_analytics.sql, on the latest engagement's
sales register; this module reads them and formats the text. Customers are tokens. Core and
pass-through revenue are shown separately; retention, cohorts and price/volume/mix are core only
and annual only. Nothing here changes data.
"""

from typing import Any

from fin.audit.errors import AnalyticsUnavailable
from fin.audit.logger import Run
from fin.db.rows import fetch_dicts
from fin.diligence.customers import UNMAPPED
from fin.diligence.engagement import company_database, rupees
from fin.forensic import measures

VIEWS = {
    "concentration": "SELECT * FROM customer_concentration ORDER BY period, segment",
    "top_customers": (
        "SELECT * FROM customer_revenue WHERE rank <= 10 ORDER BY period, segment, rank"
    ),
    "retention": "SELECT * FROM customer_retention ORDER BY period",
    "cohorts": "SELECT * FROM customer_cohorts ORDER BY cohort, period",
    "sector_mix": "SELECT * FROM sector_mix ORDER BY period, segment, revenue DESC, sector",
    "products": "SELECT * FROM product_revenue ORDER BY period, segment, revenue DESC, product",
    "pvm": "SELECT * FROM product_pvm ORDER BY period, kind, product",
    "pvm_summary": "SELECT * FROM product_pvm_summary ORDER BY period",
    "customers": "SELECT status, count(*) AS n FROM customers GROUP BY status ORDER BY status",
}


def collect(run: Run, company: str) -> dict[str, Any]:
    """Every analytics view for the latest engagement, and the period-end revenue (SPEC-20).
    E869 when it has no sales register lines."""
    with company_database(run, company) as con:
        found = con.execute("SELECT count(*) FROM analytics_sales").fetchone()
        if not found or not found[0]:
            raise AnalyticsUnavailable(
                f"{company} has no sales register lines in an engagement period; nothing to "
                f"analyse. Open an engagement (`fin engagement open {company}`) and load the "
                f"registers (`fin ingest {company} <file> --kind registers`)"
            )
        data = {name: fetch_dicts(con, sql) for name, sql in VIEWS.items()}
        data["period_end"] = measures.rows(con, run.root, company)
        return data


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value:.2%}"


def format_report(company: str, data: dict[str, Any]) -> str:
    out = [f"{company}: customer, product and sector analytics (latest engagement; Amount in Rs)"]
    out.append("customer concentration (top 1, 5, 10 share of the segment's revenue):")
    for r in data["concentration"]:
        out.append(
            f"  {r['period']} {r['segment']}: {r['customers']} customers, {rupees(r['revenue'])}; "
            f"top 1 {_pct(r['top1_share'])}, top 5 {_pct(r['top5_share'])}, "
            f"top 10 {_pct(r['top10_share'])}"
        )
    out.append("retention of core customers against the previous year:")
    for r in data["retention"]:
        out.append(
            f"  {r['period']}: net {_pct(r['net_retention'])}, gross {_pct(r['gross_retention'])}, "
            f"logo {_pct(r['logo_retention'])} ({r['retained_customers']} of "
            f"{r['prior_customers']} "
            f"kept, {r['new_customers']} new, new revenue {rupees(r['new_revenue'])})"
        )
    if not data["retention"]:
        out.append("  no period with a previous annual period in the engagement")
    out.append(
        "core cohorts (first year with revenue; opening = already customers in the first period):"
    )
    for r in data["cohorts"]:
        tag = " (opening)" if r["opening"] else ""
        out.append(
            f"  {r['cohort']}{tag} in {r['period']}: {r['active_customers']} of "
            f"{r['cohort_customers']} customers, {rupees(r['revenue'])}, revenue retention "
            f"{_pct(r['revenue_retention'])}"
        )
    out.append("sector mix:")
    for r in data["sector_mix"]:
        out.append(
            f"  {r['period']} {r['segment']} {r['sector']}: {r['customers']} customers, "
            f"{rupees(r['revenue'])} ({_pct(r['share'])})"
        )
    gap = sum(r["customers"] for r in data["sector_mix"] if r["sector"] == UNMAPPED)
    if gap:
        out.append(
            "  customers without a confirmed sector are 'unmapped': `fin map customers <co>`"
        )
    out.append("price, volume and mix of core products (priced lines):")
    for r in data["pvm_summary"]:
        out.append(
            f"  {r['period']} vs {r['prior_period']}: price {rupees(r['price_effect'])}, volume "
            f"{rupees(r['volume_effect'])}, mix {rupees(r['mix_effect'])}, new products "
            f"{rupees(r['new_revenue'])}, discontinued {rupees(r['discontinued_revenue'])}, "
            f"change {rupees(r['change'])}"
        )
    if not data["pvm_summary"]:
        out.append("  no product with a quantity and a rate in two consecutive annual periods")
    out.append("period-end revenue (invoiced in the last month and the last days of each period):")
    out += [f"  {line}" for line in measures.lines(data["period_end"])]
    return "\n".join(out)
