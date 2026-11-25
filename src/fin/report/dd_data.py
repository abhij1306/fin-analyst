"""Everything the diligence report shows (templates/report/diligence.qmd), read with SQL from the
company database: `collect` reads one company and scope.

- the engagement, its stub and the gate states (the gates it was rendered with);
- adjusted EBITDA, the bridge, the adjustments and their evidence, other income, the
  EBITDA-to-cash walk
  (views of db/schema/diligence_views.sql), and the facts behind them with their file and cell;
- core and pass-through revenue (revenue_split) with the register lines behind it;
- working capital: year-end and stub from the trial balances, monthly from the Ledger Vouchers
  (db/schema/diligence_nwc.sql), the ledgers behind them and the ones left out of the monthly view;
- the latest run of each reconciliation, the accountant queries, collections and ageing
  (bank_vs_invoices);
- the customers, sectors and pricing section (fin.report.customers), as in the health report;
- the stub's figures, labelled unaudited management accounts;
- findings, and ledger mappings not confirmed.

Python only formats: every figure, ratio and total was computed in SQL. `figures` is the narrative's
reference table (`{{figure:<name>:<period>}}`): name -> period -> value, with `FIGURES` giving
each name's
label and unit.
"""

from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DiligenceReportUnavailable
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence import gates
from fin.paths import company_db_path
from fin.report.data import customer_analytics

# name -> (label, unit, view): the figures a narrative may refer to. unit: rupees or ratio.
FIGURES: dict[str, tuple[str, str, str]] = {
    "revenue": ("Revenue as reported", "rupees", "adjusted_ebitda"),
    "adjusted_revenue": ("Adjusted revenue", "rupees", "adjusted_ebitda"),
    "reported_ebitda": ("Reported EBITDA", "rupees", "adjusted_ebitda"),
    "accepted_adjustments": ("Accepted adjustments", "rupees", "adjusted_ebitda"),
    "adjusted_ebitda": ("Adjusted EBITDA", "rupees", "adjusted_ebitda"),
    "proposed_adjustments": ("Proposed adjustments, not applied", "rupees", "adjusted_ebitda"),
    "reported_ebitda_margin": ("Reported EBITDA margin", "ratio", "adjusted_ebitda"),
    "adjusted_ebitda_margin": ("Adjusted EBITDA margin", "ratio", "adjusted_ebitda"),
    "other_income": ("Other income, below EBITDA", "rupees", "current_facts"),
    "sales_revenue": ("Sales register revenue", "rupees", "revenue_split"),
    "core_revenue": ("Core revenue", "rupees", "revenue_split"),
    "passthrough_revenue": ("Pass-through (project) revenue", "rupees", "revenue_split"),
    "passthrough_share": ("Pass-through share of sales", "ratio", "revenue_split"),
    "core_revenue_growth": ("Core revenue growth", "ratio", "revenue_split"),
    "linked_purchase_cost": ("Linked OEM purchase cost", "rupees", "revenue_split"),
    "non_cash_and_other": ("Non-cash items and other", "rupees", "ebitda_to_cash"),
    "working_capital_change": ("Working capital change", "rupees", "ebitda_to_cash"),
    "taxes_paid": ("Taxes paid", "rupees", "ebitda_to_cash"),
    "other_operating": ("Other operating", "rupees", "ebitda_to_cash"),
    "cfo": ("Net cash from operating activities", "rupees", "ebitda_to_cash"),
    "capex": ("Capital expenditure", "rupees", "ebitda_to_cash"),
    "free_cash_flow": ("Free cash flow", "rupees", "ebitda_to_cash"),
    "cash_conversion": ("Cash conversion (CFO / EBITDA)", "ratio", "ebitda_to_cash"),
    "adjusted_cash_conversion": ("Cash conversion on adjusted EBITDA", "ratio", "ebitda_to_cash"),
    "recast_revenue_effect": (
        "Recast less booked revenue (the recast's revenue effect)",
        "rupees",
        "service_recast",
    ),
    "net_working_capital": ("Net working capital", "rupees", "nwc_year_end"),
    "nwc_trade_receivables": ("Trade receivables", "rupees", "nwc_year_end"),
    "nwc_inventories": ("Inventories", "rupees", "nwc_year_end"),
    "nwc_other_current_assets": ("Other current assets", "rupees", "nwc_year_end"),
    "nwc_trade_payables": ("Trade payables", "rupees", "nwc_year_end"),
    "nwc_other_current_liabilities": ("Other current liabilities", "rupees", "nwc_year_end"),
}
_NWC_COLUMNS = {
    "net_working_capital": "net_working_capital",
    "nwc_trade_receivables": "trade_receivables",
    "nwc_inventories": "inventories",
    "nwc_other_current_assets": "other_current_assets",
    "nwc_trade_payables": "trade_payables",
    "nwc_other_current_liabilities": "other_current_liabilities",
}
_WALK_ACCOUNTS = ("cf_op_profit_before_wc", "cf_cash_generated", "cf_taxes_paid", "cf_capex")

_ENGAGEMENT = "SELECT * FROM engagement ORDER BY engagement_id DESC LIMIT 1"
_ADJUSTMENTS = """
SELECT adj_id, period, category, account, amount, revenue_effect, cost_effect, status, description,
       query_id, finding_id, source_ref
FROM adjustments WHERE engagement_id = ? AND scope = ? ORDER BY period, adj_id
"""
_OTHER_INCOME = """
SELECT period, value FROM current_facts
WHERE scope = ? AND account = 'other_income' AND period_months = 12
"""
_REGISTER_SOURCES = """
SELECT s.period, s.file_name, r.sheet, count(*) AS lines, sum(r.amount_inr) AS amount_inr
FROM register_lines r JOIN source_files s ON s.file_id = r.file_id
WHERE s.engagement_id = ? AND s.status = 'loaded' AND r.kind = 'sales' AND r.period IS NOT NULL
GROUP BY s.period, s.file_name, r.sheet, r.period ORDER BY r.period, s.file_name, r.sheet
"""
_RECON = """
SELECT r.recon_id, r.name, r.period, r.threshold, r.unmapped,
    count(*) FILTER (WHERE i.kind = 'matched') AS matched,
    count(*) FILTER (WHERE i.kind = 'difference') AS differences,
    count(*) FILTER (WHERE i.kind = 'left_only') AS left_only,
    count(*) FILTER (WHERE i.kind = 'right_only') AS right_only,
    count(*) FILTER (WHERE i.kind = 'fx') AS fx,
    count(*) FILTER (WHERE i.kind = 'not_reconciled') AS not_reconciled,
    count(*) FILTER (WHERE i.kind = 'unmapped') AS unmapped_lines,
    count(*) FILTER (WHERE i.needs_query) AS needs_query,
    count(*) FILTER (WHERE i.query_id IS NOT NULL
        OR i.item_id IN (SELECT item_id FROM query_items)) AS in_query
FROM recon_runs r LEFT JOIN recon_items i ON i.recon_id = r.recon_id
WHERE r.engagement_id = ? AND r.recon_id = (
    SELECT max(x.recon_id) FROM recon_runs x
    WHERE x.engagement_id = r.engagement_id AND x.name = r.name AND x.period = r.period)
GROUP BY r.recon_id, r.name, r.period, r.threshold, r.unmapped
ORDER BY r.period, r.name
"""
_RECAST = """
SELECT r.recon_id, r.period, i.level, i.key, i.kind, i.left_amount AS recast,
       i.right_amount AS booked,
       i.difference, i.note, i.left_source
FROM recon_runs r JOIN recon_items i ON i.recon_id = r.recon_id
WHERE r.engagement_id = ? AND r.name = 'service_recast' AND r.recon_id = (
    SELECT max(x.recon_id) FROM recon_runs x
    WHERE x.engagement_id = r.engagement_id AND x.name = r.name AND x.period = r.period)
  AND (i.level IN ('period', 'month') OR i.kind = 'undated')
ORDER BY r.period, i.level <> 'period', i.level, i.key
"""
_CONTRACTS = """
SELECT contract_id, counterparty AS customer, invoice_no, start_date, end_date, value,
       billing_terms, file_name, page
FROM contracts WHERE engagement_id = ? AND party_kind = 'customer' ORDER BY contract_id
"""
_QUERIES = """
SELECT query_id, origin, period, scope, status, amount, sources, question
FROM accountant_queries WHERE engagement_id = ? ORDER BY query_id
"""
_COLLECTIONS = """
SELECT period, count(*) AS customers, sum(invoiced_inr) AS invoiced_inr,
    sum(received_inr) AS received_inr, sum(partly_paid_inr) AS partly_paid_inr,
    sum(open_inr) AS open_inr, sum(not_due_inr) AS not_due_inr,
    sum(overdue_1_30_inr) AS overdue_1_30_inr,
    sum(overdue_31_60_inr) AS overdue_31_60_inr, sum(overdue_61_90_inr) AS overdue_61_90_inr,
    sum(overdue_over_90_inr) AS overdue_over_90_inr
FROM customer_collections GROUP BY period ORDER BY period
"""
_COLLECTION_RUNS = """
SELECT recon_id, period, as_of, due_days FROM recon_runs
WHERE name = 'bank_vs_invoices' AND engagement_id = ?
QUALIFY recon_id = max(recon_id) OVER (PARTITION BY period) ORDER BY period
"""
_FINDINGS = """
SELECT f.status, f.file_name, coalesce(f.code, f.check_name) AS code, f.period, f.label,
       f.fin_value, f.other_value
FROM findings f WHERE f.status IN ('open', 'accepted') AND f.scope = ?
ORDER BY f.status, f.file_name, f.period, f.finding_id
"""
_FACT_SOURCES = """
SELECT cf.account, cf.period, cf.value, d.file_name,
       CASE WHEN len(cf.fact_ids) > 1 THEN 'sum of ' || len(cf.fact_ids) || ': ' ELSE '' END
       || string_agg(source_ref(r.sheet, r.cell, r.page), '; ' ORDER BY f.fact_id) AS source_ref
FROM (
    SELECT *, unnest(fact_ids) AS line_id FROM current_facts WHERE scope = ? AND period_months = 12
) cf
JOIN facts f ON f.fact_id = cf.line_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = cf.doc_id
WHERE list_contains(?, cf.account) AND list_contains(?, cf.period)
GROUP BY cf.account, cf.period, cf.period_end, cf.value, cf.fact_ids, d.file_name
ORDER BY cf.account, cf.period_end
"""


def gate_states(
    con: duckdb.DuckDBPyConnection, root: Path, company: str, engagement: dict[str, Any]
) -> list[dict[str, Any]]:
    """Each gate's state as of now (an open engagement: evaluated; a closed one: the state of its
    last approval, which cannot change any more), with its title and why it is reopened."""
    if engagement["status"] == "open":
        ctx = gates.context(con, root, company)
        return [
            {"gate": g["gate"], "title": g["title"], "state": g["state"], "reason": g["reason"]}
            for g in gates.evaluate(ctx)
        ]
    approved = {
        row["gate"]: row
        for row in fetch_dicts(
            con,
            "SELECT gate, approved_at FROM gate_approvals WHERE engagement_id = ? "
            "QUALIFY row_number() OVER (PARTITION BY gate ORDER BY approval_id DESC) = 1",
            [engagement["engagement_id"]],
        )
    }
    return [
        {
            "gate": g.name,
            "title": g.title,
            "state": "approved" if g.name in approved else "open",
            "reason": "the engagement is closed",
        }
        for g in gates.GATES
    ]


def _figures(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """name -> period -> value for every FIGURES name, from the rows read."""
    out: dict[str, dict[str, Any]] = {name: {} for name in FIGURES}
    for rows_key, view in (
        ("ebitda", "adjusted_ebitda"),
        ("revenue_split", "revenue_split"),
        ("walk", "ebitda_to_cash"),
    ):
        names = [name for name, (_, _, v) in FIGURES.items() if v == view]
        for row in data[rows_key]:
            for name in names:
                out[name][row["period"]] = row[name]
    for row in data["other_income"]:
        out["other_income"][row["period"]] = row["value"]
    for row in data["recast"]:
        if row["level"] == "period":
            out["recast_revenue_effect"][row["period"]] = row["difference"]
    for row in data["nwc_year_end"]:
        for name, column in _NWC_COLUMNS.items():
            out[name][row["period"]] = row[column]
    return out


def collect(root: Path, company: str, scope: str) -> dict[str, Any]:
    """Read everything the report shows. E873 when the company has no engagement."""
    path = company_db_path(root, company)
    if not path.is_file():
        raise DiligenceReportUnavailable(f"no database for {company}; ingest its documents first")
    ensure_database(path, "company")
    with connect(path) as con:
        engagement = fetch_dicts(con, _ENGAGEMENT)
        if not engagement:
            raise DiligenceReportUnavailable(
                f"{company} has no engagement; open one first (/diligence)"
            )
        eng = engagement[0]
        eid = eng["engagement_id"]
        periods = eng["periods"].split(",")
        everything = [*periods, *([eng["stub"]] if eng["stub"] else [])]
        ebitda = fetch_dicts(
            con, "SELECT * FROM adjusted_ebitda WHERE scope = ? ORDER BY period", [scope]
        )
        accounts = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT account FROM metric_inputs "
                "WHERE scope = ? AND fact_id IS NOT NULL "
                "AND metric IN ('ebitda', 'revenue', 'cfo', 'free_cash_flow')",
                [scope],
            ).fetchall()
        ]
        data: dict[str, Any] = {
            "company": company,
            "scope": scope,
            "engagement": eng,
            "periods": periods,
            "stub": eng["stub"],
            "gates": gate_states(con, root, company, eng),
            "ebitda": ebitda,
            "bridge": fetch_dicts(
                con, "SELECT * FROM ebitda_bridge WHERE scope = ? ORDER BY period, step", [scope]
            ),
            "adjustments": fetch_dicts(con, _ADJUSTMENTS, [eid, scope]),
            "other_income": fetch_dicts(con, _OTHER_INCOME, [scope]),
            "walk": fetch_dicts(
                con, "SELECT * FROM ebitda_to_cash WHERE scope = ? ORDER BY period", [scope]
            ),
            "fact_sources": fetch_dicts(
                con, _FACT_SOURCES, [scope, [*accounts, *_WALK_ACCOUNTS, "other_income"], periods]
            ),
            "revenue_split": fetch_dicts(con, "SELECT * FROM revenue_split ORDER BY period"),
            "passthrough_lines": fetch_dicts(con, "SELECT * FROM passthrough_lines"),
            "register_sources": fetch_dicts(con, _REGISTER_SOURCES, [eid]),
            "nwc_year_end": fetch_dicts(con, "SELECT * FROM nwc_year_end ORDER BY period_end"),
            "nwc_year_end_lines": fetch_dicts(
                con, "SELECT * FROM nwc_year_end_lines ORDER BY period_end, account, ledger"
            ),
            "nwc_monthly": fetch_dicts(con, "SELECT * FROM nwc_monthly ORDER BY month_end"),
            "nwc_ledgers": fetch_dicts(
                con,
                "SELECT * EXCLUDE (side, nwc_sign) FROM nwc_monthly_ledgers "
                "ORDER BY period, status, ledger",
            ),
            "recon": fetch_dicts(con, _RECON, [eid]),
            "queries": fetch_dicts(con, _QUERIES, [eid]),
            "recast": fetch_dicts(con, _RECAST, [eid]),
            "contracts": fetch_dicts(con, _CONTRACTS, [eid]),
            "collections": fetch_dicts(con, _COLLECTIONS),
            "collection_runs": fetch_dicts(con, _COLLECTION_RUNS, [eid]),
            "collections_by_customer": fetch_dicts(
                con, "SELECT * FROM customer_collections ORDER BY period, customer"
            ),
            "customers": customer_analytics(con),
            "stub_figures": fetch_dicts(
                con,
                "SELECT period, period_start, period_end, basis, statement, account, value, "
                "ledgers, "
                "file_id "
                "FROM stub_figures ORDER BY statement, account NULLS LAST",
            ),
            "findings": fetch_dicts(con, _FINDINGS, [scope]),
            "ledgers_not_confirmed": fetch_dicts(
                con,
                "SELECT status, count(*) AS ledgers FROM ledger_accounts "
                "WHERE status <> 'confirmed' "
                "GROUP BY status ORDER BY status",
            ),
            "facts_not_confirmed": con.execute(
                "SELECT count(*) FROM facts f JOIN documents d ON d.doc_id = f.doc_id "
                "WHERE d.status = 'loaded' AND NOT f.confirmed AND f.scope = ?",
                [scope],
            ).fetchone()[0],
            "periods_shown": everything,
        }
    data["figures"] = _figures(data)
    return data
