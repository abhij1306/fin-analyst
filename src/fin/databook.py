"""The databook layout (SPEC-06): line items as rows, periods as columns, subtotals marked, and
optionally each period's share of revenue and growth on the year before. Used by
`fin statements --layout databook`, the Excel export and the Statements screen.

Every value comes from `fin.statements.statement_rows`, already converted in SQL to the display
unit (`shown`), with its share of revenue and growth computed in SQL (`of_revenue`, `yoy`);
this module only arranges and writes them.
"""

from typing import Any

from fin.config import Display
from fin.display import PER_SHARE, number, percent
from fin.statements import OTHER_LABEL, ROUNDING_NOTE, STATEMENTS, TITLES, marks

EXTRAS = {"of_revenue": "% of revenue", "yoy": "YoY %"}


def period_order(rows: list[dict[str, Any]]) -> list[str]:
    """Periods in column order: fiscal years, then other periods; oldest first."""
    order = {
        r["period"]: (r["period_months"] != 12, r["period_end"], -r["period_months"]) for r in rows
    }
    return sorted(order, key=lambda p: order[p])


def _columns(statement: str, periods: list[str], extras: tuple[str, ...]) -> list[dict[str, str]]:
    """Per period its value column, then the extras asked for (share of revenue: P&L only)."""
    kinds = [k for k in EXTRAS if k in extras and (k != "of_revenue" or statement == STATEMENTS[0])]
    out = []
    for period in periods:
        out.append({"period": period, "kind": "value", "title": period})
        out += [
            {"period": p, "kind": k, "title": f"{p} {EXTRAS[k]}"} for p in [period] for k in kinds
        ]
    return out


def _cell(row: dict[str, Any] | None, kind: str, display: Display) -> dict[str, Any]:
    if row is None:
        return {"kind": kind, "value": None, "text": ""}
    if kind != "value":
        return {"kind": kind, "value": row[kind], "text": percent(row[kind])}
    per_share = row["unit"] == PER_SHARE
    return {
        "kind": kind,
        "value": row["shown"],
        "text": number(row["shown"], display, per_share),
        "per_share": per_share,
        "line_no": row["line_no"],
        "period": row["period"],
        "source": row["source"] or "",
        "marks": marks(row),
    }


def databook(
    rows: list[dict[str, Any]], display: Display, extras: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """One table per scope and statement: {"scope", "statement", "title", "unit_label",
    "columns", "lines", "rounding"}; each line {"label", "depth", "subtotal", "other",
    "rounding", "cells"} with one cell per column (blank where the period has no such line)."""
    tables = []
    for scope in sorted({r["scope"] for r in rows}):
        for statement in STATEMENTS:
            mine = [r for r in rows if r["scope"] == scope and r["statement"] == statement]
            if not mine:
                continue
            columns = _columns(statement, period_order(mine), extras)
            lines: dict[tuple[int, str], dict[str, Any]] = {}
            for r in mine:
                line = lines.setdefault(
                    (r["line_no"], r["label"]),
                    {"label": r["label"], "depth": r["depth"], "subtotal": False,
                     "other": r["label"] == OTHER_LABEL, "rounding": False, "by_period": {}},
                )  # fmt: skip
                line["subtotal"] |= bool(r["subtotal"])
                line["rounding"] |= bool(r["rounding"])
                line["by_period"][r["period"]] = r
            for line in lines.values():
                by_period = line.pop("by_period")
                line["cells"] = [
                    _cell(by_period.get(c["period"]), c["kind"], display) for c in columns
                ]
            tables.append(
                {
                    "scope": scope,
                    "statement": statement,
                    "title": f"{TITLES[statement]}, {scope}",
                    "unit_label": display.unit_label,
                    "columns": columns,
                    "lines": [lines[key] for key in sorted(lines)],
                    "rounding": any(line["rounding"] for line in lines.values()),
                }
            )
    return tables


def format_databook(company: str, tables: list[dict[str, Any]]) -> str:
    """Plain text: per table a caption with the unit, a header of the columns, and one row per
    line; a subtotal is marked "=", a rounded total "*" (ROUNDING_NOTE under the table)."""
    out: list[str] = []
    for table in tables:
        if out:
            out.append("")
        out.append(f"{company} {table['title'].lower()} ({table['unit_label']})")
        widths = [max(12, len(c["title"])) + 2 for c in table["columns"]]
        out.append(
            f"{'':<52}"
            + "".join(f"{c['title']:>{w}}" for c, w in zip(table["columns"], widths, strict=True))
        )
        for line in table["lines"]:
            mark = "=" if line["subtotal"] else " "
            if line["rounding"]:
                mark = "*"
            label = f"{mark} " + "  " * line["depth"] + line["label"]
            cells = "".join(f"{c['text']:>{w}}" for c, w in zip(line["cells"], widths, strict=True))
            out.append((f"{label:<52}" + cells).rstrip())
        if table["rounding"]:
            out.append(f"* {ROUNDING_NOTE}")
    return "\n".join(out)
