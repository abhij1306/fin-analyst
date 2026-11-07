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
Ratios, growth and days are computed in SQL; Python only formats them for display.
"""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import ReportRenderFailed
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.statements import STATEMENTS, statement_rows
from fin.statements import TITLES as STATEMENT_TITLES

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

# Metric display names, in table order within each category. A metric not listed (one added
# later) comes after them, under its own name.
LABELS = {
    "revenue": "Revenue",
    "pat": "Profit after tax",
    "gross_margin": "Gross margin",
    "ebitda": "EBITDA",
    "ebitda_margin": "EBITDA margin",
    "ebit": "EBIT",
    "ebit_margin": "EBIT margin",
    "pbt_margin": "PBT margin",
    "pat_margin": "PAT margin",
    "roe": "ROE",
    "roce": "ROCE",
    "roa": "ROA",
    "roe_avg": "ROE (average equity)",
    "roce_avg": "ROCE (average capital)",
    "roa_avg": "ROA (average assets)",
    "current_ratio": "Current ratio",
    "quick_ratio": "Quick ratio",
    "cash_ratio": "Cash ratio",
    "debt_to_equity": "Debt / equity",
    "debt_to_ebitda": "Debt / EBITDA",
    "net_debt": "Net debt",
    "net_debt_to_ebitda": "Net debt / EBITDA",
    "interest_cover": "Interest cover",
    "debt_to_assets": "Debt / assets",
    "asset_turnover": "Asset turnover",
    "fixed_asset_turnover": "Fixed asset turnover",
    "receivable_days": "Receivable days",
    "inventory_days": "Inventory days",
    "payable_days": "Payable days",
    "cash_conversion_cycle": "Cash conversion cycle",
    "cfo": "Cash from operations",
    "free_cash_flow": "Free cash flow",
    "cfo_to_ebitda": "CFO / EBITDA",
    "cfo_to_pat": "CFO / PAT",
    "capex_to_revenue": "Capex / revenue",
    "capex_to_depreciation": "Capex / depreciation",
    "revenue_growth": "Revenue growth",
    "revenue_growth_qoq": "Revenue growth on the previous quarter",
    "ebitda_growth": "EBITDA growth",
    "pat_growth": "PAT growth",
    "total_assets_growth": "Total assets growth",
    "total_equity_growth": "Total equity growth",
    "revenue_cagr_3y": "Revenue CAGR, 3 years",
    "revenue_cagr_5y": "Revenue CAGR, 5 years",
    "ebitda_cagr_3y": "EBITDA CAGR, 3 years",
    "ebitda_cagr_5y": "EBITDA CAGR, 5 years",
    "pat_cagr_3y": "PAT CAGR, 3 years",
    "pat_cagr_5y": "PAT CAGR, 5 years",
    "total_assets_cagr_3y": "Total assets CAGR, 3 years",
    "total_assets_cagr_5y": "Total assets CAGR, 5 years",
    "total_equity_cagr_3y": "Total equity CAGR, 3 years",
    "total_equity_cagr_5y": "Total equity CAGR, 5 years",
    "other_income_to_pbt": "Other income / PBT",
    "exceptional_items_to_pbt": "Exceptional items / PBT",
    "effective_tax_rate": "Effective tax rate",
    "eps_basic": "EPS, basic",
    "eps_diluted": "EPS, diluted",
    "net_interest_income": "Net interest income",
    "cost_to_income": "Cost to income",
    "credit_cost": "Credit cost",
    "assets_to_equity": "Assets / equity",
    "ttm_revenue": "Revenue, last four quarters",
    "ttm_ebitda": "EBITDA, last four quarters",
    "ttm_pat": "PAT, last four quarters",
    "ttm_ebitda_margin": "EBITDA margin, last four quarters",
    "ttm_pat_margin": "PAT margin, last four quarters",
}
FACT_LABELS = {"revenue": "Revenue", "pat": "Profit after tax"}
# Facts shown at the top of a category table (for a year, revenue and PAT are facts).
CATEGORY_FACTS = {"growth": ["revenue", "pat"]}

# Ratios shown as times ("2.35x"); every other ratio is shown as a percentage.
TIMES = {
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "debt_to_equity",
    "debt_to_ebitda",
    "net_debt_to_ebitda",
    "interest_cover",
    "asset_turnover",
    "fixed_asset_turnover",
    "cfo_to_ebitda",
    "cfo_to_pat",
    "capex_to_depreciation",
    "assets_to_equity",
}

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

# Display unit (config.toml [display] unit) -> (rupees per unit, suffix).
MONEY_UNITS = {
    "rupees": (Decimal(1), ""),
    "thousands": (Decimal(1000), " thousand"),
    "lakhs": (Decimal(100000), " lakh"),
    "crores": (Decimal(10000000), " Cr"),
}

_METRICS_SQL = """
SELECT m.metric, m.period, m.period_months, m.value, m.unit, m.category
FROM metrics m WHERE m.scope = ?
ORDER BY m.metric, m.period_end, m.period_months DESC
"""

_PERIODS_SQL = """
SELECT DISTINCT period, period_months, period_end FROM metrics WHERE scope = ?
ORDER BY period_end, period_months DESC
"""

_FACTS_SQL = """
SELECT cf.account, cf.period, cf.period_months, cf.value, cf.fact_id, d.file_name,
       coalesce(r.sheet || '!' || r.cell, 'page ' || r.page) AS source_ref
FROM current_facts cf
JOIN facts f ON f.fact_id = cf.fact_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = cf.doc_id
WHERE cf.scope = ?
ORDER BY cf.account, cf.period_end, cf.period_months DESC
"""

_SCOPES_SQL = "SELECT DISTINCT scope FROM metrics ORDER BY scope"

_DEFINITIONS_SQL = "SELECT metric, category, unit, definition, formula FROM metric_definitions"

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


def _rows(con: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    result = con.execute(sql, params)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _scope_data(con: Any, scope: str) -> dict[str, Any]:
    """Metrics (every period), periods and facts of one scope."""
    periods = _rows(con, _PERIODS_SQL, [scope])
    metrics: dict[str, dict[str, Decimal | None]] = {}
    units: dict[str, str] = {}
    categories: dict[str, str] = {}
    for row in _rows(con, _METRICS_SQL, [scope]):
        metrics.setdefault(row["metric"], {})[row["period"]] = row["value"]
        units[row["metric"]] = row["unit"]
        categories[row["metric"]] = row["category"]
    return {
        "years": [p["period"] for p in periods if p["period_months"] == 12],
        "quarters": [p["period"] for p in periods if p["period_months"] == 3],
        "periods": [p["period"] for p in periods],
        "metrics": metrics,
        "metric_units": units,
        "metric_categories": categories,
        "facts": _rows(con, _FACTS_SQL, [scope]),
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
        definitions = _rows(con, _DEFINITIONS_SQL, [])
        metric_accounts = [row[0] for row in con.execute(_METRIC_ACCOUNTS_SQL).fetchall()]
        unconfirmed = _rows(con, _UNCONFIRMED_SQL, [scope])
        missing = _rows(con, _MISSING_SQL, [scope])
        findings = _rows(con, _FINDINGS_SQL, [scope])
        coverage = _rows(con, _COVERAGE_SQL, [scope, list(STATEMENTS)])
        unread = [
            {"file_name": file_name, **page}
            for file_name, pages in con.execute(_UNREAD_SQL).fetchall()
            for page in json.loads(pages)
        ]
    if not main["years"]:
        raise ReportRenderFailed(
            f"{company} has no annual {scope} data to report on: the health report covers fiscal "
            "years (load an annual report, or results with a 'Year ended' column); quarter and "
            "half-year figures are in `fin query` and the dashboard"
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
        "definitions": sorted(
            (d for d in definitions if d["metric"] in present),
            key=lambda d: (_category_order(d["category"]), _metric_order(d["metric"])),
        ),
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


def _metric_order(metric: str) -> tuple[int, str]:
    labels = list(LABELS)
    return (labels.index(metric) if metric in labels else len(labels), metric)


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


# Display formatting.


def money(value: Decimal | None, display_unit: str) -> str:
    if value is None:
        return "n/a"
    per_unit, suffix = MONEY_UNITS[display_unit]
    amount = (value / per_unit).quantize(Decimal("0.01"), ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    return f"{sign}₹{abs(amount):,.2f}{suffix}"


def metric_text(metric: str, unit: str, value: Decimal | None, display_unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "rupees":
        return money(value, display_unit)
    if unit == "rupees_per_share":
        return f"₹{value.quantize(Decimal('0.01'), ROUND_HALF_UP)}"
    if unit == "days":
        return f"{value.quantize(Decimal('0.1'), ROUND_HALF_UP)} days"
    if metric in TIMES:
        return f"{value.quantize(Decimal('0.01'), ROUND_HALF_UP)}x"
    return f"{(value * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)}%"


def label(metric: str) -> str:
    return LABELS.get(metric, metric)


def cell(data: dict[str, Any], kind: str, name: str, period: str, display_unit: str) -> str:
    value = value_of(data, kind, name, period)
    if kind == "fact":
        return money(value, display_unit)
    return metric_text(name, data["metric_units"][name], value, display_unit)


def table(header: list[str], rows: list[list[str]], empty: str) -> str:
    """A Markdown pipe table, or `empty` (a sentence) when there are no rows."""
    if not rows:
        return empty

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    return "\n".join([line(header), "|" + "|".join("---" for _ in header) + "|", *map(line, rows)])


def trend_table(
    data: dict[str, Any], rows: list[tuple[str, str]], periods: list[str], display_unit: str
) -> str:
    body = [
        [FACT_LABELS[name] if kind == "fact" else label(name)]
        + [cell(data, kind, name, p, display_unit) for p in periods]
        for kind, name in rows
    ]
    return table(["", *periods], body, "")


def category_rows(data: dict[str, Any], category: str) -> list[tuple[str, str]]:
    """The facts and metrics a category table shows: metrics with rows in this scope's years."""
    metrics = [
        m
        for m, c in data["metric_categories"].items()
        if c == category and any(p in data["metrics"][m] for p in data["years"])
    ]
    facts = [("fact", a) for a in CATEGORY_FACTS.get(category, []) if metrics]
    return facts + [("metric", m) for m in sorted(metrics, key=_metric_order)]


def summary_tables(data: dict[str, Any], display_unit: str) -> str:
    """One key-metrics table per scope the company has (both scopes where present)."""
    parts = []
    for scope, s in data["summary"].items():
        body = []
        for kind, name, values in s["rows"]:
            unit = "rupees" if kind == "fact" else data["metric_units"].get(name, "ratio")
            text = (
                [money(values[y], display_unit) for y in s["years"]]
                if kind == "fact"
                else [metric_text(name, unit, values[y], display_unit) for y in s["years"]]
            )
            body.append([FACT_LABELS[name] if kind == "fact" else label(name), *text])
        parts.append(f"**{scope.capitalize()}**\n\n" + table(["", *s["years"]], body, ""))
    return "\n\n".join(parts)


def quarters_table(data: dict[str, Any], display_unit: str) -> str:
    quarters = data["quarters"][-QUARTERS_SHOWN:]
    rows = [
        ("metric", m)
        for m in QUARTER_ROWS
        if m in data["metrics"] and any(q in data["metrics"][m] for q in quarters)
    ]
    return trend_table(data, rows, quarters, display_unit)


def peers_table(data: dict[str, Any], display_unit: str) -> str:
    peers = data["peers"]
    if peers["year"] is None:
        return (
            f"No fiscal year is loaded for every company ({', '.join(peers['companies'])}) in "
            f"{data['scope']} figures, so there is no comparison."
        )
    year = peers["year"]
    values: dict[tuple[str, str], dict[str, Decimal | None]] = {}
    for company, rows in peers["rows"].items():
        for kind, name, by_year in rows:
            values.setdefault((kind, name), {})[company] = by_year[year]
    body = []
    for kind, name in KEY_ROWS:
        if (kind, name) not in values:
            continue
        cells = []
        for company in peers["companies"]:
            value = values[(kind, name)].get(company)
            if kind == "fact":
                cells.append(money(value, display_unit))
            else:
                cells.append(metric_text(name, data["metric_units"][name], value, display_unit))
        body.append([FACT_LABELS[name] if kind == "fact" else label(name), *cells])
    return f"Fiscal year {year}, {data['scope']}.\n\n" + table(["", *peers["companies"]], body, "")


def statements_tables(data: dict[str, Any], display_unit: str) -> str:
    """The condensed statements: subtotals only, one table per statement."""
    parts = []
    years = data["years"][-YEARS_SHOWN:]
    for statement in STATEMENTS:
        lines: dict[tuple[int, str], dict[str, Decimal]] = {}
        for r in data["statements"]:
            if r["statement"] == statement:
                lines.setdefault((r["line_no"], r["label"]), {})[r["period"]] = r["value"]
        if not lines:
            continue
        body = [
            [name, *(money(by_year.get(y), display_unit) for y in years)]
            for (_, name), by_year in sorted(lines.items())
        ]
        title = STATEMENT_TITLES[statement]
        parts.append(f"### {title}\n\n" + table(["", *years], body, ""))
    return "\n\n".join(parts) or "No statements."


def definitions_table(data: dict[str, Any]) -> str:
    return table(
        ["Category", "Metric", "Definition", "Formula"],
        [
            [
                d["category"].replace("_", " "),
                f"{label(d['metric'])} (`{d['metric']}`)",
                d["definition"],
                f"`{d['formula']}`",
            ]
            for d in data["definitions"]
        ],
        "No metrics.",
    )


def findings_table(data: dict[str, Any], display_unit: str) -> str:
    def with_source(value: Decimal | None, source: str | None, file: str | None = None) -> str:
        where = ", ".join(x for x in (file, source) if x)
        return money(value, display_unit) + (f" ({where})" if where else "")

    return table(
        ["Finding", "Document", "Check", "Period", "Line", "This document", "Compared with"],
        [
            [
                row["status"],
                f"{row['file_name'] or ''} ({row['document_status']})",
                row["code"],
                row["period"] or "",
                row["label"] or "",
                with_source(row["fin_value"], row["fin_source"]),
                with_source(row["other_value"], row["other_source"], row["other_file"]),
            ]
            for row in data["findings"]
        ],
        "No open or accepted findings.",
    )


def coverage_tables(data: dict[str, Any]) -> dict[str, str]:
    coverage = table(
        ["Statement", "Figures read", "Mapped", "Mapped share", "Read by Claude, unverified"],
        [
            [
                STATEMENT_TITLES[row["statement"]],
                str(row["figures"]),
                str(row["mapped"]),
                metric_text("mapped_share", "ratio", row["mapped_share"], "rupees"),
                str(row["claude_unverified"]),
            ]
            for row in data["coverage"]
        ],
        "No figures loaded.",
    )
    unread = table(
        ["File", "Statement", "Page", "Reason"],
        [
            [row["file_name"], row["statement"], str(row["page"]), f"{row['code']} {row['reason']}"]
            for row in data["unread"]
        ],
        "None: fin read every statement page of the loaded documents.",
    )
    unconfirmed = table(
        ["File", "Label", "Periods"],
        [[row["file_name"], row["raw_label"], row["years"]] for row in data["unconfirmed"]],
        "No unconfirmed mappings.",
    )
    missing = table(
        ["Year", "Metric", "Missing inputs"],
        [
            [row["fiscal_year"], label(row["metric"]), row["missing_inputs"]]
            for row in data["missing"]
        ],
        "No missing data: every metric has a value.",
    )
    return {"coverage": coverage, "unread": unread, "unconfirmed": unconfirmed, "missing": missing}


def facts_table(data: dict[str, Any], display_unit: str) -> str:
    used = set(data["metric_accounts"])
    years = set(data["years"])
    return table(
        ["Account", "Year", "Value", "File", "Source"],
        [
            [
                f"`{row['account']}`",
                row["period"],
                money(row["value"], display_unit),
                row["file_name"],
                row["source_ref"],
            ]
            for row in data["facts"]
            if row["account"] in used and row["period"] in years
        ],
        "No facts.",
    )
