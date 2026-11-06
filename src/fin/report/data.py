"""Everything a report shows, read with SQL, plus display formatting and the data hash.

`collect` reads one company and scope. The health report is annual: it reads fiscal years only
(period_months = 12); quarter and half-year periods are for `fin query` and the dashboard.
- metrics (the `metrics` view) and every current fact (`current_facts`) with its cell or page;
- data quality: unconfirmed mappings and NULL metrics with their missing inputs (company database),
  and failed validations (audit `validation_results`; failed documents were rolled back, so they are
  only there).

`data_hash` is the SHA-256 of all of it as sorted JSON, so the same data always gives the same hash.
Ratios and days are computed in SQL; Python only formats them for display.
"""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import ReportRenderFailed
from fin.db.conn import connect
from fin.paths import audit_db_path, company_db_path

# (key, title, rows). A row is ("metric", name) or ("fact", account).
SECTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("growth", "Growth", [("fact", "revenue"), ("metric", "revenue_growth"), ("fact", "pat")]),
    (
        "profitability",
        "Profitability",
        [
            ("metric", "gross_margin"),
            ("metric", "ebitda"),
            ("metric", "ebitda_margin"),
            ("metric", "pat_margin"),
            ("metric", "roce"),
            ("metric", "roe"),
        ],
    ),
    (
        "cash_flow",
        "Cash flow quality",
        [
            ("fact", "cfo"),
            ("metric", "cfo_to_ebitda"),
            ("metric", "free_cash_flow"),
            ("metric", "capex_to_revenue"),
        ],
    ),
    (
        "working_capital",
        "Working capital",
        [
            ("metric", "receivable_days"),
            ("metric", "inventory_days"),
            ("metric", "payable_days"),
            ("metric", "cash_conversion_cycle"),
        ],
    ),
    (
        "leverage",
        "Leverage and liquidity",
        [
            ("metric", "debt_to_ebitda"),
            ("metric", "interest_cover"),
            ("metric", "current_ratio"),
            ("metric", "debt_to_equity"),
        ],
    ),
    (
        "earnings_quality",
        "Earnings quality",
        [("fact", "pat"), ("fact", "cfo"), ("fact", "other_income"), ("metric", "cfo_to_ebitda")],
    ),
    ("observations", "Observations", []),
    ("data_quality", "Data quality", []),
]
TITLES = {key: title for key, title, _ in SECTIONS}

LABELS = {
    "revenue": "Revenue",
    "pat": "Profit after tax",
    "cfo": "Cash from operations",
    "other_income": "Other income",
    "revenue_growth": "Revenue growth",
    "gross_margin": "Gross margin",
    "ebitda": "EBITDA",
    "ebitda_margin": "EBITDA margin",
    "pat_margin": "PAT margin",
    "roce": "ROCE",
    "roe": "ROE",
    "cfo_to_ebitda": "CFO / EBITDA",
    "free_cash_flow": "Free cash flow",
    "capex_to_revenue": "Capex / revenue",
    "receivable_days": "Receivable days",
    "inventory_days": "Inventory days",
    "payable_days": "Payable days",
    "cash_conversion_cycle": "Cash conversion cycle",
    "debt_to_ebitda": "Debt / EBITDA",
    "interest_cover": "Interest cover",
    "current_ratio": "Current ratio",
    "debt_to_equity": "Debt / equity",
}

# Ratio metrics shown as a percentage; the other ratios are shown as times ("2.35x").
PERCENT = {
    "revenue_growth",
    "gross_margin",
    "ebitda_margin",
    "pat_margin",
    "capex_to_revenue",
    "roce",
    "roe",
}

# Display unit (config.toml [display] unit) -> (rupees per unit, suffix).
MONEY_UNITS = {
    "rupees": (Decimal(1), ""),
    "thousands": (Decimal(1000), " thousand"),
    "lakhs": (Decimal(100000), " lakh"),
    "crores": (Decimal(10000000), " Cr"),
}

_METRICS_SQL = """
SELECT metric, fiscal_year, value, unit FROM metrics WHERE scope = ? AND period_months = 12
ORDER BY metric, fiscal_year
"""

_FACTS_SQL = """
SELECT cf.account, cf.fiscal_year, cf.value, cf.fact_id, d.file_name,
       coalesce(r.sheet || '!' || r.cell, 'page ' || r.page) AS source_ref
FROM current_facts cf
JOIN facts f ON f.fact_id = cf.fact_id
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = cf.doc_id
WHERE cf.scope = ? AND cf.period_months = 12
ORDER BY cf.account, cf.fiscal_year
"""

_METRIC_ACCOUNTS_SQL = "SELECT DISTINCT account FROM metric_definitions ORDER BY account"

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

_FAILED_VALIDATIONS_SQL = """
SELECT CAST(recorded_at AS DATE) AS checked_on, file_name, check_name, fiscal_year, expected,
       actual, error_code
FROM validation_results
WHERE company = ? AND NOT passed AND (scope = ? OR scope IS NULL)
ORDER BY recorded_at, file_name, check_name, fiscal_year
"""


def _rows(con: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    result = con.execute(sql, params)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def collect(root: Path, company: str, scope: str) -> dict[str, Any]:
    """Read everything the report shows. Raises E601 when the company has no metrics in `scope`."""
    with connect(company_db_path(root, company)) as con:
        metric_rows = _rows(con, _METRICS_SQL, [scope])
        facts = _rows(con, _FACTS_SQL, [scope])
        metric_accounts = [row[0] for row in con.execute(_METRIC_ACCOUNTS_SQL).fetchall()]
        unconfirmed = _rows(con, _UNCONFIRMED_SQL, [scope])
        missing = _rows(con, _MISSING_SQL, [scope])
    with connect(audit_db_path(root)) as con:
        failed = _rows(con, _FAILED_VALIDATIONS_SQL, [company, scope])
    if not metric_rows:
        raise ReportRenderFailed(
            f"{company} has no annual {scope} data to report on: the health report covers fiscal "
            "years (load an annual report, or results with a 'Year ended' column); quarter and "
            "half-year figures are in `fin query` and the dashboard"
        )
    metrics: dict[str, dict[str, Decimal | None]] = {}
    units: dict[str, str] = {}
    for row in metric_rows:
        metrics.setdefault(row["metric"], {})[row["fiscal_year"]] = row["value"]
        units[row["metric"]] = row["unit"]
    return {
        "company": company,
        "scope": scope,
        "years": sorted({row["fiscal_year"] for row in metric_rows}),
        "metrics": metrics,
        "metric_units": units,
        "facts": facts,
        "metric_accounts": metric_accounts,
        "unconfirmed": unconfirmed,
        "missing": missing,
        "failed_validations": failed,
    }


def to_json(data: Any) -> str:
    """Sorted JSON with exact decimals and dates as text."""
    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False, indent=1)


def data_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(to_json(data).encode("utf-8")).hexdigest()


def fact_value(data: dict[str, Any], account: str, year: str) -> Decimal | None:
    for row in data["facts"]:
        if row["account"] == account and row["fiscal_year"] == year:
            return row["value"]
    return None


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
    if unit == "days":
        return f"{value.quantize(Decimal('0.1'), ROUND_HALF_UP)} days"
    if metric in PERCENT:
        return f"{(value * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)}%"
    return f"{value.quantize(Decimal('0.01'), ROUND_HALF_UP)}x"


def table(header: list[str], rows: list[list[str]], empty: str) -> str:
    """A Markdown pipe table, or `empty` (a sentence) when there are no rows."""
    if not rows:
        return empty

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"

    return "\n".join([line(header), "|" + "|".join("---" for _ in header) + "|", *map(line, rows)])


def section_table(data: dict[str, Any], rows: list[tuple[str, str]], display_unit: str) -> str:
    years = data["years"]
    body = []
    for kind, name in rows:
        if kind == "metric":
            unit = data["metric_units"][name]
            values = data["metrics"][name]
            cells = [metric_text(name, unit, values.get(year), display_unit) for year in years]
        else:
            cells = [money(fact_value(data, name, year), display_unit) for year in years]
        body.append([LABELS[name], *cells])
    return table(["", *years], body, "")


def data_quality_tables(data: dict[str, Any], display_unit: str) -> dict[str, str]:
    unconfirmed = table(
        ["File", "Label", "Years"],
        [[row["file_name"], row["raw_label"], row["years"]] for row in data["unconfirmed"]],
        "No unconfirmed mappings.",
    )
    failed = table(
        ["Checked", "File", "Check", "Year", "Expected", "Actual", "Code"],
        [
            [
                str(row["checked_on"]),
                row["file_name"] or "",
                row["check_name"],
                row["fiscal_year"] or "",
                money(row["expected"], display_unit),
                money(row["actual"], display_unit),
                row["error_code"] or "",
            ]
            for row in data["failed_validations"]
        ],
        "No failed validations.",
    )
    missing = table(
        ["Year", "Metric", "Missing inputs"],
        [
            [row["fiscal_year"], LABELS[row["metric"]], row["missing_inputs"]]
            for row in data["missing"]
        ],
        "No missing data: every metric has a value.",
    )
    used = set(data["metric_accounts"])
    facts = table(
        ["Account", "Year", "Value", "File", "Source"],
        [
            [
                f"`{row['account']}`",
                row["fiscal_year"],
                money(row["value"], display_unit),
                row["file_name"],
                row["source_ref"],
            ]
            for row in data["facts"]
            if row["account"] in used
        ],
        "No facts.",
    )
    return {
        "unconfirmed": unconfirmed,
        "failed_validations": failed,
        "missing_data": missing,
        "facts": facts,
    }
