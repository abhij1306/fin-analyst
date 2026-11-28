"""The Statements screen (SPEC-06, SPEC-14): company, scope, statement, periods and unit pickers;
the statements in the databook layout (`fin.databook`) or reported, rebuilt and difference; each
figure opens its source (file and sheet!cell or page) and marks; the Excel export. Read-only: the
export runs as a job (`fin.ui.job_routes`). Every number is SQL's (`statement_rows`)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, FinError
from fin.config import DISPLAY_UNITS, Display, load_config
from fin.databook import EXTRAS, databook
from fin.display import PER_SHARE, display_settings, number
from fin.rebuilt.compare import blocks, notes, rebuilt_rows, select_period
from fin.services.jobs import list_jobs
from fin.statements import TITLES, latest_annual_period, marks, statement_rows

VIEWS = ("reported", "rebuilt")
STATEMENT_CHOICES = {"all": "All statements", **TITLES}
PERIODS = {"years": "Fiscal years", "quarters": "Quarters and half years", "all": "All periods"}
EXPORT_NAME = "{company}_financials.xlsx"


def _pick(query: Mapping[str, str], key: str, choices: Any, default: str) -> str:
    """The picker's value, or `default`. E001 for a value not among `choices`."""
    value = query.get(key) or default
    if value not in choices:
        raise DependencyMissing(
            f"no statements {key} {value!r}; use {' or '.join(choices)}"
        ).with_context(**{key: value})
    return value


def _reported(
    rows: list[dict[str, Any]], query: Mapping[str, str], display: Display, default_scope: str
) -> dict[str, Any]:
    scopes = sorted({r["scope"] for r in rows})
    scope = _pick(query, "scope", scopes, default_scope if default_scope in scopes else scopes[0])
    statement = _pick(query, "statement", STATEMENT_CHOICES, "all")
    periods = _pick(query, "periods", PERIODS, "years")
    extras = tuple(k for k in EXTRAS if query.get(k) == "1")
    shown = [
        r for r in rows
        if r["scope"] == scope
        and statement in ("all", r["statement"])
        and (periods == "all" or (r["period_months"] == 12) == (periods == "years"))
    ]  # fmt: skip
    return {
        "tables": databook(shown, display, extras),
        "scopes": scopes,
        "scope": scope,
        "statement": statement,
        "statements": STATEMENT_CHOICES,
        "periods": periods,
        "period_choices": PERIODS,
        "extras": extras,
        "extra_choices": EXTRAS,
    }


def _rebuilt(root: Path, company: str, display: Display) -> dict[str, Any]:
    result = rebuilt_rows(root, company, display=display)
    result = select_period(result, latest_annual_period(result["rows"]))
    shown = {row["period"] for row in result["rows"]}
    return {"blocks": blocks(result), "notes": notes(result, shown)}


def statements_screen(
    root: Path, company: str, query: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The screen's data for the pickers in `query` (view, scope, statement, periods, unit,
    of_revenue, yoy), or why there is nothing to show. E001 for a picker value not offered."""
    query = query or {}
    view = _pick(query, "view", VIEWS, "reported")
    display = display_settings(root, company, query.get("unit") or None)
    data: dict[str, Any]
    try:
        if view == "rebuilt":
            data = _rebuilt(root, company, display)
        else:
            rows = statement_rows(root, company, display=display)
    except FinError as error:  # nothing loaded yet, or no trial balance to rebuild from
        data = {"message": f"{error.code}: {error.message}"}
    else:
        if view == "reported":  # a picker value not offered is refused (E001), as a bad view
            data = _reported(rows, query, display, load_config(root).default_scope)
    export = EXPORT_NAME.format(company=company)
    return {
        **data,
        "view": view,
        "views": VIEWS,
        "unit": display.unit,
        "units": tuple(DISPLAY_UNITS),
        "unit_label": display.unit_label,
        "export": export if (root / "reports" / company / export).is_file() else None,
        "jobs": [j for j in list_jobs(root, company) if j["action"] == "export"][:1],
        "job_labels": {"export": "Export to Excel"},
    }


def statement_source(root: Path, company: str, query: Mapping[str, str]) -> dict[str, Any]:
    """One figure of the screen: its line, period, value, source and marks. E001 when the scope,
    period and line given name no figure."""
    scope, period, line = query.get("scope"), query.get("period"), query.get("line", "")
    display = display_settings(root, company, query.get("unit") or None)
    rows = statement_rows(root, company, period, scope, display) if scope and period else []
    found = [r for r in rows if line.isdigit() and r["line_no"] == int(line)]
    if not found:
        raise DependencyMissing(
            f"no figure of {company} at {scope} {period} line {line!r}"
        ).with_context(scope=scope, period=period, line=line)
    row = found[0]
    return {
        "label": row["label"],
        "title": f"{row['scope']} {row['period']}: {TITLES[row['statement']].lower()}",
        "value": number(row["shown"], display, row["unit"] == PER_SHARE),
        "unit_label": "₹ per share" if row["unit"] == PER_SHARE else display.unit_label,
        "rupees": row["value"],
        "sources": (row["source"] or "").split("; ") if row["source"] else [],
        "marks": marks(row),
    }
