"""The data of the diligence screens (SPEC-09): Engagement, and Bridge with its two views, the
EBITDA bridge and the adjusted P&L. Read with fin's own modules; numbers are the ones the views
return, shown in the documents' unit as `fin engagement bridge` shows them."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, EngagementState, FinError
from fin.audit.logger import Run
from fin.diligence import adjusted, adjustments, bridge, engagement, gates, queries
from fin.diligence.engagement import rupees
from fin.paths import company_db_path
from fin.statements import TITLES, amount, in_unit, latest_annual_period, statement_rows, unit_title

BRIDGE_VIEWS = ("bridge", "pl")
AS_REPORTED = ("balance_sheet", "cash_flow")  # no adjustment kind changes them yet


def screen_run(screen: str, root: Path, company: str) -> Run:
    # Screens only read: the run is not started, so there is no run record.
    return Run(f"ui {screen}", {"company": company}, root)


def with_engagement(root: Path, company: str, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The screen's data, or why there is none: nothing loaded yet, or no engagement (E803)."""
    if not company_db_path(root, company).is_file():
        return {"no_engagement": f"Nothing is loaded for {company} yet, so there is no engagement."}
    try:
        return load()
    except EngagementState as error:
        return {"no_engagement": f"{error.code}: {error.message}", "can_open": True}


def money(value: Decimal | None, unit: str) -> str:
    return "-" if value is None else amount(in_unit(value, unit))


def _engagement(root: Path, company: str) -> dict[str, Any]:
    run = screen_run("engagement", root, company)
    status = engagement.engagement_status(run, company)
    row = status["engagement"]
    return {
        "engagement": {**row, "query_threshold": rupees(row["query_threshold"])},
        "periods": queries.periods_of(row),
        "queries": status["queries"],
        "adjustments": {
            name: {"count": item["count"], "amount": rupees(item["amount"])}
            for name, item in status["adjustments"].items()
        },
        "gates": gates.gate_status(run, company)["gates"],
    }


def engagement_screen(root: Path, company: str) -> dict[str, Any]:
    """The open engagement and where it stands, or the form to open one."""
    return with_engagement(root, company, lambda: _engagement(root, company))


def _adjustment(row: dict[str, Any], unit: str) -> dict[str, Any]:
    return {
        "adj_id": row["adj_id"],
        "status": row["status"],
        "category": row["category"],
        "description": row["description"],
        "amount": money(row["amount"], unit),
        "evidence": adjustments.evidence_text(row),
    }


def _bridge(root: Path, company: str) -> dict[str, Any]:
    run = screen_run("bridge", root, company)
    result = bridge.bridge(run, company)
    unit = result["unit"]
    rows = bridge.adjustment_rows(run, company)
    by_id = {row["adj_id"]: row for row in rows}
    why = {(n["period"], n["scope"]): n["message"] for n in result["not_computed"]}
    blocks = []
    for summary in result["adjusted_ebitda"]:
        key = (summary["period"], summary["scope"])
        steps = [
            {
                "kind": step["kind"],
                "label": step["description"],
                "amount": money(step["amount"], unit),
                "adjustment": _adjustment(by_id[step["adj_id"]], unit) if step["adj_id"] else None,
            }
            for step in result["bridge"]
            if (step["period"], step["scope"]) == key
        ]
        others = [r for r in rows if (r["period"], r["scope"]) == key and r["status"] != "accepted"]
        blocks.append(
            {
                "title": f"{summary['scope']} {summary['period']}",
                "steps": steps,
                "others": [_adjustment(r, unit) for r in others],
                "proposed": money(summary["proposed_adjustments"], unit),
                "proposed_count": summary["proposed_count"],
                "note": why.get(key),
            }
        )
    return {
        "engagement": engagement.format_engagement(result["engagement"]),
        "unit": unit_title(unit),
        "blocks": blocks,
    }


def _as_reported(root: Path, company: str) -> dict[str, Any]:
    """The latest fiscal year's balance sheet and cash flow as reported, or why there are none."""
    try:
        rows = statement_rows(root, company)
    except FinError as error:
        return {"message": f"{error.code}: {error.message}", "blocks": []}
    rows = [r for r in rows if r["period"] == latest_annual_period(rows)]
    out: list[dict[str, Any]] = []
    for r in (r for r in rows if r["statement"] in AS_REPORTED):
        key = (r["scope"], r["statement"])
        if not out or out[-1]["key"] != key:
            title = f"{r['scope']} {r['period']}: {TITLES[r['statement']].lower()}, as reported"
            out.append({"key": key, "title": title, "lines": []})
        value = amount(in_unit(r["value"], r["unit"]))
        out[-1]["lines"].append({"label": r["label"], "depth": r["depth"], "value": value})
    return {"message": None, "blocks": out}


def _pl(root: Path, company: str) -> dict[str, Any]:
    run = screen_run("bridge", root, company)
    result = adjusted.adjusted_pl(run, company)
    unit = result["unit"]
    blocks: dict[tuple[str, str], dict[str, Any]] = {}
    for line in result["lines"]:
        block = blocks.setdefault(
            (line["period"], line["scope"]),
            {"title": f"{line['scope']} {line['period']}", "lines": []},
        )
        block["lines"].append(
            {
                "label": line["label"],
                "depth": line["depth"],
                "reported": money(line["reported"], unit),
                "adjustment": money(line["adjustment"], unit),
                "adjusted": money(line["adjusted"], unit),
            }
        )
    ebitda = {(row["period"], row["scope"]): row for row in result["ebitda"]}
    for key, block in blocks.items():
        row = ebitda.get(key)
        if row is not None:
            block["ebitda"] = {
                "reported": money(row["reported_ebitda"], unit),
                "adjustment": money(row["accepted_adjustments"], unit),
                "adjusted": money(row["adjusted_ebitda"], unit),
            }
    return {
        "engagement": engagement.format_engagement(result["engagement"]),
        "unit": unit_title(unit),
        "blocks": list(blocks.values()),
        "as_reported": _as_reported(root, company),
    }


def bridge_screen(root: Path, company: str, view: str | None = None) -> dict[str, Any]:
    """The latest engagement's EBITDA bridge, or (view "pl") its adjusted P&L. E001 for a view
    that is not one of BRIDGE_VIEWS."""
    chosen = view or "bridge"
    if chosen not in BRIDGE_VIEWS:
        raise DependencyMissing(f"no bridge view {view!r}; use bridge or pl")
    load = _bridge if chosen == "bridge" else _pl
    data = with_engagement(root, company, lambda: load(root, company))
    return {**data, "view": chosen, "views": BRIDGE_VIEWS}
