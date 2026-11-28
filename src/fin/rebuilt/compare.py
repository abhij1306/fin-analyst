"""Reported, rebuilt and difference side by side (`fin statements --rebuilt`, the export's
rebuilt sheets and the Statements screen's rebuilt view; SPEC-14).

Each line of the standalone `standard_statements` (reported) and of `rebuilt_statements` is
joined by period, statement and account; difference = rebuilt - reported, computed in SQL, and
NULL when either side has no figure (missing is not zero). Lines only one side has are shown with
the other blank. Money is shown in the display unit, as `fin statements`: each of reported,
rebuilt, difference and unmapped converted in SQL (`*_shown`, `fin.display`).
"""

from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, RebuildNotPossible
from fin.config import Display
from fin.db.conn import connect, no_company_message
from fin.db.rows import fetch_dicts
from fin.display import PER_SHARE, display_settings, number, sql_params
from fin.paths import company_db_path
from fin.rebuilt.view import add_views, readiness
from fin.statements import STATEMENTS, TITLES

_COMPARE_SQL = """
SELECT c.*,
    display_value(reported, CASE WHEN unit = 'rupees' THEN $per_unit ELSE 1 END,
        CASE WHEN unit = 'rupees' THEN $decimals ELSE 2 END) AS reported_shown,
    display_value(rebuilt, CASE WHEN unit = 'rupees' THEN $per_unit ELSE 1 END,
        CASE WHEN unit = 'rupees' THEN $decimals ELSE 2 END) AS rebuilt_shown,
    display_value(difference, CASE WHEN unit = 'rupees' THEN $per_unit ELSE 1 END,
        CASE WHEN unit = 'rupees' THEN $decimals ELSE 2 END) AS difference_shown,
    display_value(unmapped, CASE WHEN unit = 'rupees' THEN $per_unit ELSE 1 END,
        CASE WHEN unit = 'rupees' THEN $decimals ELSE 2 END) AS unmapped_shown
FROM (
WITH r AS (SELECT * FROM rebuilt_statements WHERE $period IS NULL OR period = $period),
s AS (
    SELECT * FROM standard_statements
    WHERE scope = 'standalone' AND period IN (SELECT period FROM r)
)
SELECT
    coalesce(r.period, s.period) AS period,
    coalesce(r.period_months, s.period_months) AS period_months,
    coalesce(r.period_end, s.period_end) AS period_end,
    coalesce(r.statement, s.statement) AS statement,
    coalesce(r.line_no, s.line_no) AS line_no,
    coalesce(r.depth, s.depth) AS depth,
    coalesce(r.line_type, s.line_type) AS line_type,
    coalesce(r.account, s.account) AS account,
    coalesce(r.label, s.label) AS label,
    s.value AS reported,
    r.value AS rebuilt,
    r.value - s.value AS difference,
    coalesce(r.incomplete, false) AS incomplete,
    r.unmapped, r.unmapped_ledgers, r.ledgers, r.sources, r.note, s.fact_ids,
    CASE WHEN a.unit = 'rupees_per_share' THEN a.unit ELSE 'rupees' END AS unit,
    EXISTS (
        SELECT 1 FROM statement_paths p WHERE p.ancestor = coalesce(r.account, s.account)
    ) AS subtotal
FROM r
FULL JOIN s ON s.period = r.period AND s.statement = r.statement AND s.account = r.account
LEFT JOIN statement_accounts a ON a.account = coalesce(r.account, s.account)
) c
ORDER BY period_end, period, array_position($statements, statement), line_no, label
"""


def _check(company: str, years: list[dict[str, Any]], period: str | None) -> None:
    """E822 when the period asked for, or every period, cannot be rebuilt."""
    known = {year["period"]: year for year in years}
    if period is not None and period not in known:
        raise RebuildNotPossible(
            f"{period} is not a period of {company}'s engagement ({', '.join(known)})"
        ).with_context(company=company, period=period)
    if period is not None and not known[period]["ready"]:
        raise RebuildNotPossible(known[period]["ask"]).with_context(company=company, period=period)
    if not any(year["ready"] for year in years):
        asks = "; ".join(year["ask"] for year in years)
        raise RebuildNotPossible(
            f"{company}: no year has a ledger-level trial balance to rebuild from: {asks}"
        ).with_context(company=company)


def rebuilt_rows(
    root: Path, company: str, period: str | None = None, display: Display | None = None
) -> dict[str, Any]:
    """{"rows": reported, rebuilt and difference per line (rupees, and `*_shown` in `display`,
    default the company's), "years": `readiness` of each period, "unit_label": the display's
    caption}. E001 without a company database; E822 when nothing (or not the period asked for)
    can be rebuilt."""
    db = company_db_path(root, company)
    if not db.is_file():
        raise DependencyMissing(no_company_message(company))
    display = display or display_settings(root, company)
    with connect(db) as con:
        add_views(con, root)
        years = readiness(con, company)
        _check(company, years, period)
        params = {"period": period, "statements": list(STATEMENTS), **sql_params(display)}
        rows = fetch_dicts(con, _COMPARE_SQL, params)
    return {"rows": rows, "years": years, "unit_label": display.unit_label, "display": display}


def _money(row: dict[str, Any], column: str, display: Display) -> str:
    return number(row[f"{column}_shown"], display, row["unit"] == PER_SHARE)


def flag(row: dict[str, Any], display: Display) -> str:
    """What a reader must know about a rebuilt line: why it has no value, or that unmapped
    ledgers sit under it."""
    if row["note"]:
        return str(row["note"])
    if row["line_type"] == "unmapped":
        return f"{row['unmapped_ledgers']} ledgers not confirmed to an account (`fin map ledgers`)"
    if not row["incomplete"]:
        return ""
    if row["unmapped"] is None:
        return (
            f"incomplete: {row['unmapped_ledgers']} unmapped ledgers in this year or the year "
            "before"
        )
    return (
        f"incomplete: {row['unmapped_ledgers']} unmapped ledgers under it, "
        f"{_money(row, 'unmapped', display)} {display.unit_label} (debit - credit)"
    )


def blocks(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One block per period and statement, its lines as display text in the display unit."""
    out: list[dict[str, Any]] = []
    display = result["display"]
    for row in result["rows"]:
        key = (row["period"], row["statement"])
        if not out or out[-1]["key"] != key:
            title = f"{row['period']}: {TITLES[row['statement']].lower()}"
            out.append({"key": key, "title": title, "lines": []})
        out[-1]["lines"].append(
            {
                "label": row["label"],
                "depth": row["depth"],
                "line_type": row["line_type"],
                "subtotal": row["subtotal"],
                "reported": _money(row, "reported", display),
                "rebuilt": _money(row, "rebuilt", display),
                "difference": _money(row, "difference", display),
                "flag": flag(row, display),
                "sources": row["sources"] or "",
            }
        )
    return out


def notes(result: dict[str, Any], periods: set[str]) -> list[str]:
    """What the company must send for a period that is not rebuilt, and which shown years have
    no cash flow."""
    out = [year["ask"] for year in result["years"] if not year["ready"]]
    out += [
        f"{year['period']}: no cash flow: the trial balance of the year before is not loaded"
        for year in result["years"]
        if year["ready"] and not year["cash_flow"] and year["period"] in periods
    ]
    return out


def format_rebuilt(company: str, result: dict[str, Any]) -> str:
    """Plain text: one block per period and statement, the reported, rebuilt and difference
    columns, and a flag after a line that needs one; then the notes."""
    out: list[str] = []
    for block in blocks(result):
        if out:
            out.append("")
        out.append(
            f"{company} standalone {block['title']}, reported and rebuilt from the trial balance "
            f"({result['unit_label']})"
        )
        out.append(f"{'':<56} {'Reported':>18} {'Rebuilt':>18} {'Difference':>18}")
        for line in block["lines"]:
            label = "  " * line["depth"] + line["label"]
            text = (
                f"{label:<56} {line['reported']:>18} {line['rebuilt']:>18} {line['difference']:>18}"
            )
            if line["flag"]:
                text += f"  ({line['flag']})"
            out.append(text.rstrip())
    shown = {row["period"] for row in result["rows"]}
    for note in notes(result, shown):
        out.append(f"note: {note}")
    return "\n".join(out)


def select_period(result: dict[str, Any], period: str) -> dict[str, Any]:
    """The result with the rows of one period only."""
    return {**result, "rows": [row for row in result["rows"] if row["period"] == period]}
