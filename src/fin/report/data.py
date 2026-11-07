"""Everything a report shows, read with SQL, plus display formatting and the data hash.

`collect` reads one company and scope (and, for the summary, the company's other scope):
- metrics (the `metrics` view) for every period, and every current fact with its cell or page;
- the standard statements (`fin.statements`), condensed to subtotals, for the appendix;
- metric definitions (`metric_definitions`) for the metrics the report shows;
- findings open or accepted (`fin.findings`): accepted ones are in the figures as filed; a held
  document (with open findings) is not;
- data coverage: mapped share per statement and unverified Claude-read figures
  (`statement_coverage`), statement pages fin could not read (`documents.unread_pages`),
  unconfirmed mappings, and NULL metrics with their missing inputs;
- peers (optional): the key metrics of other companies for the latest fiscal year all have.

`data_hash` is the SHA-256 of all of it as sorted JSON, so the same data always gives the same hash.
Ratios, growth and days are computed in SQL; `fin.report.tables` only formats them for display.
"""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import ReportRenderFailed
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.statements import STATEMENTS, statement_rows

YEARS_SHOWN = 5  # fiscal years in the trend tables (the latest ones)
QUARTERS_SHOWN = 5

# Category sections, in report order: (key, title). A category with no metric in the data is left
# out (lender metrics exist only for NBFCs and banks).
CATEGORIES = [
    ("profitability", "Profitability"),
    ("returns", "Returns"),
    ("liquidity", "Liquidity"),
    ("solvency", "Solvency"),
    ("efficiency", "Efficiency and working capital"),
    ("cash_flow", "Cash flow"),
    ("growth", "Growth"),
    ("earnings_quality", "Earnings quality"),
    ("per_share", "Per share"),
    ("lender", "Lending"),
]
# Narrative sections: key -> heading in narrative.md and the report.
TITLES = {
    "summary": "Summary",
    **dict(CATEGORIES),
    "quarters": "Latest quarters",
    "peers": "Peer comparison",
    "findings": "Findings",
    "coverage": "Data coverage",
}
# Facts shown at the top of a category table (for a year, revenue and PAT are facts).
CATEGORY_FACTS = {"growth": ["revenue", "pat"]}

# One chart per category where a trend picture helps: metrics of one unit, at most four.
CHARTS = {
    "profitability": ["gross_margin", "ebitda_margin", "pat_margin"],
    "returns": ["roe", "roce", "roa"],
    "liquidity": ["current_ratio", "quick_ratio", "cash_ratio"],
    "solvency": ["debt_to_equity", "debt_to_ebitda", "net_debt_to_ebitda"],
    "efficiency": ["receivable_days", "inventory_days", "payable_days", "cash_conversion_cycle"],
    "cash_flow": ["cfo", "free_cash_flow"],
    "growth": ["revenue_growth", "ebitda_growth", "pat_growth"],
    "per_share": ["eps_basic", "eps_diluted"],
}

# The summary and peer tables: ("fact" | "metric", name).
KEY_ROWS = [
    ("fact", "revenue"),
    ("metric", "revenue_growth"),
    ("metric", "ebitda"),
    ("metric", "ebitda_margin"),
    ("fact", "pat"),
    ("metric", "pat_margin"),
    ("metric", "roce"),
    ("metric", "roe"),
    ("metric", "debt_to_equity"),
    ("metric", "current_ratio"),
    ("metric", "cfo"),
    ("metric", "free_cash_flow"),
]

# The latest quarters table (quarters only).
QUARTER_ROWS = [
    "revenue",
    "revenue_growth",
    "revenue_growth_qoq",
    "ebitda",
    "ebitda_margin",
    "pat",
    "pat_margin",
    "ttm_revenue",
    "ttm_ebitda",
    "ttm_pat",
    "ttm_ebitda_margin",
    "ttm_pat_margin",
]

_METRICS_SQL = """
SELECT m.metric, m.period, m.period_months, m.value
FROM metrics m WHERE m.scope = ?
ORDER BY m.metric, m.period_end, m.period_months DESC
"""

_PERIODS_SQL = """
SELECT DISTINCT period, period_months, period_end FROM metrics WHERE scope = ?
ORDER BY period_end, period_months DESC
"""

_FACTS_SQL = """
SELECT cf.account, cf.period, cf.period_months, cf.value, cf.fact_id, d.file_name,
       source_ref(r.sheet, r.cell, r.page) AS source_ref
FROM current_facts cf
JOIN facts f ON f.fact_id = cf.fact_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = cf.doc_id
WHERE cf.scope = ?
ORDER BY cf.account, cf.period_end, cf.period_months DESC
"""

_SCOPES_SQL = "SELECT DISTINCT scope FROM metrics ORDER BY scope"

_DEFINITIONS_SQL = """
SELECT metric, label, times, category, unit, definition, formula FROM metric_definitions
ORDER BY position
"""

_METRIC_ACCOUNTS_SQL = "SELECT DISTINCT account FROM metric_input_accounts ORDER BY account"

_UNCONFIRMED_SQL = """
SELECT d.file_name, r.raw_label,
       array_to_string(list_sort(list_distinct(list(f.period))), ', ') AS years
FROM facts f
JOIN documents d ON d.doc_id = f.doc_id
JOIN raw_values r ON r.raw_id = f.raw_id
WHERE d.status = 'loaded' AND NOT f.confirmed AND f.scope = ?
GROUP BY d.file_name, r.raw_label
ORDER BY d.file_name, r.raw_label
"""

_MISSING_SQL = """
SELECT m.metric, m.fiscal_year,
       coalesce(nullif(array_to_string(list_sort(list_distinct(
           list(i.account || CASE WHEN i.input_fiscal_year <> m.fiscal_year
                                  THEN ' (' || i.input_fiscal_year || ')' ELSE '' END)
           FILTER (WHERE i.fact_id IS NULL))), ', '), ''),
           'none (a denominator is zero)') AS missing_inputs
FROM metrics m
LEFT JOIN metric_inputs i ON i.metric = m.metric AND i.period = m.period AND i.scope = m.scope
WHERE m.scope = ? AND m.period_months = 12 AND m.value IS NULL
GROUP BY m.metric, m.fiscal_year
ORDER BY m.fiscal_year, m.metric
"""

_FINDINGS_SQL = """
SELECT f.status, d.status AS document_status, f.file_name, coalesce(f.code, f.check_name) AS code,
       f.period, f.label, f.fin_value, f.fin_source, f.other_value, f.other_source, f.other_file
FROM findings f
JOIN documents d ON d.doc_id = f.doc_id
WHERE f.status IN ('open', 'accepted') AND f.scope = ?
ORDER BY f.status, f.file_name, f.period, f.finding_id
"""

_COVERAGE_SQL = """
SELECT statement, figures, mapped, mapped_share, claude_unverified
FROM statement_coverage WHERE scope = ?
ORDER BY array_position(?, statement)
"""

_UNREAD_SQL = """
SELECT file_name, unread_pages FROM documents
WHERE status = 'loaded' AND unread_pages IS NOT NULL ORDER BY doc_id
"""


def _scope_data(con: Any, scope: str) -> dict[str, Any]:
    """Metrics (every period), periods and facts of one scope."""
    periods = fetch_dicts(con, _PERIODS_SQL, [scope])
    metrics: dict[str, dict[str, Decimal | None]] = {}
    for row in fetch_dicts(con, _METRICS_SQL, [scope]):
        metrics.setdefault(row["metric"], {})[row["period"]] = row["value"]
    return {
        "years": [p["period"] for p in periods if p["period_months"] == 12],
        "quarters": [p["period"] for p in periods if p["period_months"] == 3],
        "periods": [p["period"] for p in periods],
        "metrics": metrics,
        "facts": fetch_dicts(con, _FACTS_SQL, [scope]),
    }


def _key_values(scope_data: dict[str, Any], years: list[str]) -> list[list[Any]]:
    """[kind, name, {year: value}] for the KEY_ROWS the scope has."""
    out = []
    for kind, name in KEY_ROWS:
        if kind == "metric" and name not in scope_data["metrics"]:
            continue
        out.append([kind, name, {year: value_of(scope_data, kind, name, year) for year in years}])
    return out


def _condensed_statements(root: Path, company: str, scope: str, years: list[str]) -> list[Any]:
    """Subtotal lines (accounts that have lines) of each statement, for `years`."""
    rows = statement_rows(root, company, scope=scope)
    return [
        {
            "statement": r["statement"],
            "line_no": r["line_no"],
            "label": r["label"],
            "period": r["period"],
            "value": r["value"],
        }
        for r in rows
        if r["period"] in years and r["line_type"] == "line" and r["explained"] is not None
    ]


def _peers(root: Path, company: str, scope: str, peers: list[str], own: dict[str, Any]) -> Any:
    """The key metrics of `company` and each peer for the latest fiscal year all of them have in
    `scope`. Raises E601 for a peer with no database."""
    data = {company: own}
    for peer in peers:
        db = company_db_path(root, peer)
        if not db.is_file():
            raise ReportRenderFailed(f"no database for peer {peer}; ingest its documents first")
        ensure_database(db, "company")
        with connect(db) as con:
            data[peer] = _scope_data(con, scope)
    common = set.intersection(*(set(d["years"]) for d in data.values()))
    year = max(common, key=lambda y: own["years"].index(y)) if common else None
    return {
        "year": year,
        "companies": list(data),
        "rows": {name: _key_values(d, [year] if year else []) for name, d in data.items()},
    }


def collect(root: Path, company: str, scope: str, peers: list[str] | None = None) -> dict[str, Any]:
    """Read everything the report shows. Raises E601 when the company has no annual metrics in
    `scope`, or a peer has no database."""
    with connect(company_db_path(root, company)) as con:
        scopes = [r[0] for r in con.execute(_SCOPES_SQL).fetchall()]
        by_scope = {s: _scope_data(con, s) for s in scopes}
        main = by_scope.get(scope) or _scope_data(con, scope)
        definitions = fetch_dicts(con, _DEFINITIONS_SQL, [])
        metric_accounts = [row[0] for row in con.execute(_METRIC_ACCOUNTS_SQL).fetchall()]
        unconfirmed = fetch_dicts(con, _UNCONFIRMED_SQL, [scope])
        missing = fetch_dicts(con, _MISSING_SQL, [scope])
        findings = fetch_dicts(con, _FINDINGS_SQL, [scope])
        coverage = fetch_dicts(con, _COVERAGE_SQL, [scope, list(STATEMENTS)])
        unread = [
            {"file_name": file_name, **page}
            for file_name, pages in con.execute(_UNREAD_SQL).fetchall()
            for page in json.loads(pages)
        ]
    if not main["years"]:
        raise ReportRenderFailed(
            f"{company} has no annual {scope} data to report on: the health report covers fiscal "
            "years (load an annual report, or results with a 'Year ended' column); quarter and "
            "half-year figures are in `fin sql` and the dashboard"
        )
    shown = main["years"][-YEARS_SHOWN:]
    present = set(main["metrics"])
    return {
        "company": company,
        "scope": scope,
        **main,
        "summary": {
            s: {
                "years": d["years"][-YEARS_SHOWN:],
                "rows": _key_values(d, d["years"][-YEARS_SHOWN:]),
            }
            for s, d in by_scope.items()
            if d["years"]
        },
        "statements": _condensed_statements(root, company, scope, shown),
        # In report order: by category, then metric_definitions order.
        "definitions": sorted(
            (d for d in definitions if d["metric"] in present),
            key=lambda d: _category_order(d["category"]),
        ),
        # Every metric's definition (unit, label, times), for values from any scope or peer.
        "metric_info": {d["metric"]: d for d in definitions},
        "metric_accounts": metric_accounts,
        "unconfirmed": unconfirmed,
        "missing": missing,
        "findings": findings,
        "coverage": coverage,
        "unread": unread,
        "peers": _peers(root, company, scope, peers, main) if peers else None,
    }


def _category_order(category: str) -> int:
    keys = [key for key, _ in CATEGORIES] + ["ttm", "headline"]
    return keys.index(category) if category in keys else len(keys)


def to_json(data: Any) -> str:
    """Sorted JSON with exact decimals and dates as text."""
    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False, indent=1)


def data_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(data).encode("utf-8")).hexdigest()


def fact_value(data: dict[str, Any], account: str, period: str) -> Decimal | None:
    for row in data["facts"]:
        if row["account"] == account and row["period"] == period:
            return row["value"]
    return None


def value_of(data: dict[str, Any], kind: str, name: str, period: str) -> Decimal | None:
    if kind == "fact":
        return fact_value(data, name, period)
    return data["metrics"].get(name, {}).get(period)
