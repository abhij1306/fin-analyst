"""Display formatting and the report's Markdown tables, from the data `fin.report.data.collect`
read. Python only formats here: every value was computed in SQL.

A metric's display name (`label`), whether a ratio shows as times (`times`) and its order come from
`metric_definitions` (in `data["definitions"]`).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fin.extract.units import RUPEES_PER_UNIT
from fin.report.data import (
    CATEGORY_FACTS,
    KEY_ROWS,
    QUARTER_ROWS,
    QUARTERS_SHOWN,
    YEARS_SHOWN,
    value_of,
)
from fin.statements import STATEMENTS, amount
from fin.statements import TITLES as STATEMENT_TITLES

# Display unit (config.toml [display] unit) -> (rupees per unit, suffix).
MONEY_UNITS = {
    display: (RUPEES_PER_UNIT[unit], suffix)
    for display, unit, suffix in (
        ("rupees", "rupees", ""),
        ("thousands", "thousand", " thousand"),
        ("lakhs", "lakh", " lakh"),
        ("crores", "crore", " Cr"),
    )
}
FACT_LABELS = {"revenue": "Revenue", "pat": "Profit after tax"}


def money(value: Decimal | None, display_unit: str) -> str:
    if value is None:
        return "n/a"
    per_unit, suffix = MONEY_UNITS[display_unit]
    return amount((value / per_unit).quantize(Decimal("0.01"), ROUND_HALF_UP), "₹") + suffix


def metric_text(unit: str, value: Decimal | None, display_unit: str, times: bool = False) -> str:
    """A metric value for display: money in `display_unit`, days, a ratio as times ("2.35x") when
    `times`, else as a percentage."""
    if value is None:
        return "n/a"
    if unit == "rupees":
        return money(value, display_unit)
    if unit == "rupees_per_share":
        return f"₹{value.quantize(Decimal('0.01'), ROUND_HALF_UP)}"
    if unit == "days":
        return f"{value.quantize(Decimal('0.1'), ROUND_HALF_UP)} days"
    if times:
        return f"{value.quantize(Decimal('0.01'), ROUND_HALF_UP)}x"
    return f"{(value * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)}%"


def _definition(data: dict[str, Any], metric: str) -> dict[str, Any]:
    return next(d for d in data["definitions"] if d["metric"] == metric)


def label(data: dict[str, Any], metric: str) -> str:
    return _definition(data, metric)["label"]


def is_times(data: dict[str, Any], metric: str) -> bool:
    return _definition(data, metric)["times"]


def metric_value_text(
    data: dict[str, Any], metric: str, value: Decimal | None, display_unit: str
) -> str:
    """A value of `metric` for display, in the metric's unit."""
    return metric_text(data["metric_units"][metric], value, display_unit, is_times(data, metric))


def _row_label(data: dict[str, Any], kind: str, name: str) -> str:
    return FACT_LABELS[name] if kind == "fact" else label(data, name)


def _cell(data: dict[str, Any], kind: str, name: str, period: str, display_unit: str) -> str:
    value = value_of(data, kind, name, period)
    if kind == "fact":
        return money(value, display_unit)
    return metric_value_text(data, name, value, display_unit)


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
        [_row_label(data, kind, name)] + [_cell(data, kind, name, p, display_unit) for p in periods]
        for kind, name in rows
    ]
    return table(["", *periods], body, "")


def category_rows(data: dict[str, Any], category: str) -> list[tuple[str, str]]:
    """The facts and metrics a category table shows: metrics with rows in this scope's years, in
    metric_definitions order."""
    metrics = [
        d["metric"]
        for d in data["definitions"]
        if d["category"] == category
        and any(p in data["metrics"][d["metric"]] for p in data["years"])
    ]
    facts = [("fact", a) for a in CATEGORY_FACTS.get(category, []) if metrics]
    return facts + [("metric", m) for m in metrics]


def summary_tables(data: dict[str, Any], display_unit: str) -> str:
    """One key-metrics table per scope the company has (both scopes where present)."""
    parts = []
    for scope, s in data["summary"].items():
        body = []
        for kind, name, values in s["rows"]:
            if kind == "fact":
                text = [money(values[y], display_unit) for y in s["years"]]
            else:
                text = [metric_value_text(data, name, values[y], display_unit) for y in s["years"]]
            body.append([_row_label(data, kind, name), *text])
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
                cells.append(metric_value_text(data, name, value, display_unit))
        body.append([_row_label(data, kind, name), *cells])
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
                f"{d['label']} (`{d['metric']}`)",
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
                metric_text("ratio", row["mapped_share"], "rupees"),
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
            [row["fiscal_year"], label(data, row["metric"]), row["missing_inputs"]]
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
