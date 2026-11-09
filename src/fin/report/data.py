"""Everything a report shows, read with SQL, plus display formatting and the data hash.

`collect` reads one company and scope (and, for the summary, the company's other scope):
- metrics (the `metrics` view) for every period, and every current fact with its unit and the cell
  or page of each line behind it (a value summed from several lines lists them all);
- the appendix facts: those behind the metrics of the periods the tables show (both scopes),
  through `metric_inputs`, plus the facts the tables show themselves (the key facts and the
  condensed statements' subtotals), and those behind the narrative's references (a fact, or a
  metric's inputs, of any period);
- the standard statements (`fin.statements`), condensed to the printed subtotals, for the appendix;
- metric definitions (`metric_definitions`) for the metrics the report shows;
- findings open or accepted (`fin.findings`): accepted ones are in the figures as filed; a held
  document (with open findings) is not;
- data coverage: mapped share per statement (`statement_coverage`), statement pages fin could not
  read (`documents.unread_pages`), unconfirmed mappings, and missing data: the NULL metrics the
  report shows, with their scope and missing inputs (`_wanted`: every metric of the years shown,
  the quarter table's of the quarters shown, the other scope's summary table, and the narrative's
  metric references);
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
from fin.map.dictionary import load_accounts
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

# Every current fact with the cell or page of each line behind it: a value summed from several
# printed lines (current_facts.fact_ids) lists them all, "sum of N: ...".
_FACTS_SQL = """
SELECT cf.scope, cf.account, cf.period, cf.period_months, cf.value, cf.fact_ids, d.file_name,
       CASE WHEN len(cf.fact_ids) > 1 THEN 'sum of ' || len(cf.fact_ids) || ': ' ELSE '' END
       || string_agg(source_ref(r.sheet, r.cell, r.page), '; ' ORDER BY f.fact_id) AS source_ref
FROM (SELECT *, unnest(fact_ids) AS line_id FROM current_facts WHERE scope = ?) cf
JOIN facts f ON f.fact_id = cf.line_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = cf.doc_id
GROUP BY cf.scope, cf.account, cf.period, cf.period_months, cf.period_end, cf.value, cf.fact_ids,
         d.file_name
ORDER BY cf.account, cf.period_end, cf.period_months DESC
"""

_SCOPES_SQL = "SELECT DISTINCT scope FROM metrics ORDER BY scope"

_DEFINITIONS_SQL = """
SELECT metric, label, times, category, unit, definition, formula FROM metric_definitions
ORDER BY position
"""

# The facts behind the metrics of the shown periods (`scope period` strings) and behind single
# metrics (`scope metric period` strings: the narrative's references), for the appendix.
_USED_SQL = """
SELECT DISTINCT scope, account, input_period FROM metric_inputs
WHERE fact_id IS NOT NULL AND (list_contains(?, scope || ' ' || period)
    OR list_contains(?, scope || ' ' || metric || ' ' || period))
"""

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

# NULL metrics among those the report shows (`scope metric period` strings, see `_wanted`).
_MISSING_SQL = """
SELECT m.scope, m.metric, m.period,
       coalesce(nullif(array_to_string(list_sort(list_distinct(
           list(i.account || CASE WHEN i.input_period <> m.period
                                  THEN ' (' || i.input_period || ')' ELSE '' END)
           FILTER (WHERE i.fact_id IS NULL))), ', '), ''),
           'none (a denominator is zero)') AS missing_inputs
FROM metrics m
LEFT JOIN metric_inputs i ON i.metric = m.metric AND i.period = m.period AND i.scope = m.scope
WHERE m.value IS NULL AND list_contains(?, m.scope || ' ' || m.metric || ' ' || m.period)
GROUP BY m.scope, m.metric, m.period, m.period_end, m.period_months
ORDER BY m.scope <> ?, m.scope, m.period_end, m.period_months DESC, m.metric
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
SELECT statement, figures, mapped, mapped_share
FROM statement_coverage WHERE scope = ?
ORDER BY array_position(?, statement)
"""

_UNREAD_SQL = """
SELECT file_name, unread_pages FROM documents
WHERE status = 'loaded' AND unread_pages IS NOT NULL ORDER BY doc_id
"""


def _scope_data(con: Any, scope: str, units: dict[str, str]) -> dict[str, Any]:
    """Metrics (every period), periods and facts of one scope. Each fact has its account's `unit`
    from `units` (the dictionary's: rupees, or rupees_per_share for EPS)."""
    periods = fetch_dicts(con, _PERIODS_SQL, [scope])
    metrics: dict[str, dict[str, Decimal | None]] = {}
    for row in fetch_dicts(con, _METRICS_SQL, [scope]):
        metrics.setdefault(row["metric"], {})[row["period"]] = row["value"]
    return {
        "years": [p["period"] for p in periods if p["period_months"] == 12],
        "quarters": [p["period"] for p in periods if p["period_months"] == 3],
        "periods": [p["period"] for p in periods],
        "metrics": metrics,
        "facts": [
            row | {"unit": units[row["account"]]} for row in fetch_dicts(con, _FACTS_SQL, [scope])
        ],
    }


def _shown(main: dict[str, Any], scope: str, by_scope: dict[str, Any]) -> dict[str, list[str]]:
    """The periods the report's tables show, per scope: this scope's latest years and quarters, and
    the other scope's latest years (its summary table)."""
    shown = {scope: main["years"][-YEARS_SHOWN:] + main["quarters"][-QUARTERS_SHOWN:]}
    return shown | {
        s: d["years"][-YEARS_SHOWN:] for s, d in by_scope.items() if s != scope and d["years"]
    }


def _appendix(
    con: Any,
    scope: str,
    shown: dict[str, list[str]],
    facts: dict[str, list[dict[str, Any]]],
    statements: list[dict[str, Any]],
    references: set[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """The facts behind the shown periods' metrics (their inputs, earlier periods included), the
    facts the tables show themselves (KEY_ROWS and the condensed `statements` of `scope`), and the
    facts behind the narrative's `references` (kind, name, period), each with its source."""
    keys = [f"{s} {p}" for s, periods in shown.items() for p in periods]
    metric_keys = [f"{scope} {name} {p}" for kind, name, p in references if kind == "metric"]
    used = {tuple(row) for row in con.execute(_USED_SQL, [keys, metric_keys]).fetchall()}
    used |= {
        (s, name, p)
        for s, periods in shown.items()
        for p in periods
        for kind, name in KEY_ROWS
        if kind == "fact"
    }
    used |= {(scope, r["account"], r["period"]) for r in statements}
    used |= {(scope, name, p) for kind, name, p in references if kind == "fact"}
    return [row for s in shown for row in facts[s] if (s, row["account"], row["period"]) in used]


def _wanted(
    scope: str,
    main: dict[str, Any],
    shown: dict[str, list[str]],
    references: set[tuple[str, str, str]],
) -> list[str]:
    """`scope metric period` of every metric value the report shows: in `scope`, every metric of
    the years shown and the quarter table's of the quarters shown; in the other scope, the summary
    table's; and the narrative's metric references."""
    summary = [name for kind, name in KEY_ROWS if kind == "metric"]
    wanted = [
        f"{s} {name} {p}"
        for s, periods in shown.items()
        for p in periods
        for name in (
            summary if s != scope else main["metrics"] if p in main["years"] else QUARTER_ROWS
        )
    ]
    return wanted + [f"{scope} {name} {p}" for kind, name, p in references if kind == "metric"]


def _key_values(scope_data: dict[str, Any], years: list[str]) -> list[list[Any]]:
    """[kind, name, {year: value}] for the KEY_ROWS the scope has."""
    out = []
    for kind, name in KEY_ROWS:
        if kind == "metric" and name not in scope_data["metrics"]:
            continue
        out.append([kind, name, {year: value_of(scope_data, kind, name, year) for year in years}])
    return out


def _condensed_statements(
    root: Path, company: str, scope: str, years: list[str], subtotals: set[str]
) -> list[Any]:
    """The printed subtotals (`subtotals`: accounts with lines in the dictionary tree) of each
    statement, for `years`, whether or not any line under them is mapped. `complete` is false when
    the mapped lines under it do not add up to the printed figure exactly (`fin statements` then
    shows an "Unmapped / other" line); a subtotal with no mapped line under it is printed as is."""
    rows = statement_rows(root, company, scope=scope)
    return [
        {
            "statement": r["statement"],
            "line_no": r["line_no"],
            "account": r["account"],
            "label": r["label"],
            "period": r["period"],
            "value": r["value"],
            "complete": r["explained"] is None or r["explained"] == r["value"],
        }
        for r in rows
        if r["period"] in years and r["line_type"] == "line" and r["account"] in subtotals
    ]


def _peers(
    root: Path,
    company: str,
    scope: str,
    peers: list[str],
    own: dict[str, Any],
    units: dict[str, str],
) -> Any:
    """The key metrics of `company` and each peer for the latest fiscal year all of them have in
    `scope`. Raises E601 for a peer with no database."""
    data = {company: own}
    for peer in peers:
        db = company_db_path(root, peer)
        if not db.is_file():
            raise ReportRenderFailed(f"no database for peer {peer}; ingest its documents first")
        ensure_database(db, "company")
        with connect(db) as con:
            data[peer] = _scope_data(con, scope, units)
    common = set.intersection(*(set(d["years"]) for d in data.values()))
    year = max(common, key=lambda y: own["years"].index(y)) if common else None
    return {
        "year": year,
        "companies": list(data),
        "rows": {name: _key_values(d, [year] if year else []) for name, d in data.items()},
    }


def collect(
    root: Path,
    company: str,
    scope: str,
    peers: list[str] | None = None,
    references: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Read everything the report shows. `references` are the narrative's (kind, name, period),
    for the appendix and the missing data. Raises E601 when the company has no annual metrics in
    `scope`, or a peer has no database."""
    references = references or set()
    accounts = load_accounts(root)
    units = {name: account.unit for name, account in accounts.items()}
    with connect(company_db_path(root, company)) as con:
        scopes = [r[0] for r in con.execute(_SCOPES_SQL).fetchall()]
        by_scope = {s: _scope_data(con, s, units) for s in scopes}
        main = by_scope.get(scope) or _scope_data(con, scope, units)
        if not main["years"]:
            raise ReportRenderFailed(
                f"{company} has no annual {scope} data to report on: the health report covers "
                "fiscal years (load an annual report, or results with a 'Year ended' column); "
                "quarter and half-year figures are in `fin sql` and the dashboard"
            )
    shown = _shown(main, scope, by_scope)
    statements = _condensed_statements(
        root,
        company,
        scope,
        main["years"][-YEARS_SHOWN:],
        {a.parent for a in accounts.values() if a.parent},
    )
    with connect(company_db_path(root, company)) as con:
        facts = {s: d["facts"] for s, d in by_scope.items()}
        appendix = _appendix(con, scope, shown, facts, statements, references)
        definitions = fetch_dicts(con, _DEFINITIONS_SQL, [])
        unconfirmed = fetch_dicts(con, _UNCONFIRMED_SQL, [scope])
        missing = fetch_dicts(con, _MISSING_SQL, [_wanted(scope, main, shown, references), scope])
        findings = fetch_dicts(con, _FINDINGS_SQL, [scope])
        coverage = fetch_dicts(con, _COVERAGE_SQL, [scope, list(STATEMENTS)])
        unread = [
            {"file_name": file_name, **page}
            for file_name, pages in con.execute(_UNREAD_SQL).fetchall()
            for page in json.loads(pages)
        ]
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
        "statements": statements,
        # In report order: by category, then metric_definitions order.
        "definitions": sorted(
            (d for d in definitions if d["metric"] in present),
            key=lambda d: _category_order(d["category"]),
        ),
        # Every metric's definition (unit, label, times), for values from any scope or peer.
        "metric_info": {d["metric"]: d for d in definitions},
        # The facts behind every figure the tables and the narrative show (both scopes).
        "appendix": appendix,
        "unconfirmed": unconfirmed,
        "missing": missing,
        "findings": findings,
        "coverage": coverage,
        "unread": unread,
        "peers": _peers(root, company, scope, peers, main, units) if peers else None,
    }


def _category_order(category: str) -> int:
    keys = [key for key, _ in CATEGORIES] + ["ttm", "headline"]
    return keys.index(category) if category in keys else len(keys)


def to_json(data: Any) -> str:
    """Sorted JSON with exact decimals and dates as text."""
    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False, indent=1)


def data_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(data).encode("utf-8")).hexdigest()


def fact_row(data: dict[str, Any], account: str, period: str) -> dict[str, Any] | None:
    for row in data["facts"]:
        if row["account"] == account and row["period"] == period:
            return row
    return None


def value_of(data: dict[str, Any], kind: str, name: str, period: str) -> Decimal | None:
    if kind == "fact":
        row = fact_row(data, name, period)
        return None if row is None else row["value"]
    return data["metrics"].get(name, {}).get(period)
