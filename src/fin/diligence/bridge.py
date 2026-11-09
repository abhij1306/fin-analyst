"""`fin engagement bridge`: the EBITDA bridge and the EBITDA-to-cash walk.

The figures are the views in db/schema/diligence_views.sql (`adjusted_ebitda`, `ebitda_bridge`,
`ebitda_to_cash`, also queryable with `fin sql`), for the latest engagement, open or closed. This
module only reads and formats them. Text: money in the unit the company's documents use, as
`fin statements` shows it (fin.statements.document_unit), ratios as percentages; a line without a
value shows "-" and names its missing inputs. `--json`: the view rows, money in rupees.
"""

from typing import Any

from fin.audit.errors import EngagementState
from fin.audit.logger import Run
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, format_engagement
from fin.statements import amount, document_unit, in_unit, unit_title

# The walk's money lines, in order (ebitda_to_cash columns), and the columns each is made from: a
# line without a value names those of its inputs that have none.
WALK_LINES = (
    ("ebitda", "EBITDA", ("ebitda",)),
    ("non_cash_and_other", "Non-cash and other items", ("cf_op_profit_before_wc", "ebitda")),
    (
        "working_capital_change",
        "Working capital change",
        ("cf_cash_generated", "cf_op_profit_before_wc"),
    ),
    ("taxes_paid", "Taxes paid", ("cf_taxes_paid",)),
    ("other_operating", "Other operating items", ("cfo", "cf_cash_generated", "taxes_paid")),
    ("cfo", "Cash from operations", ("cfo",)),
    ("capex", "Capex", ("capex",)),
    ("free_cash_flow", "Free cash flow", ("cfo", "capex")),
)
WALK_RATIOS = (
    ("cash_conversion", "Cash conversion (CFO / EBITDA)", ("cfo", "ebitda")),
    (
        "adjusted_cash_conversion",
        "Adjusted cash conversion (CFO / adjusted EBITDA)",
        ("cfo", "ebitda"),
    ),
)
# Walk columns named by the account they come from, in "missing" notes.
ACCOUNTS = {"capex": "cf_capex", "taxes_paid": "cf_taxes_paid", "reported_ebitda": "ebitda"}


def latest_engagement(con: Any, company: str) -> dict[str, Any]:
    """The latest engagement, open or closed. E803 when the company has none."""
    rows = fetch_dicts(con, "SELECT * FROM engagement ORDER BY engagement_id DESC LIMIT 1")
    if not rows:
        raise EngagementState(
            f"{company} has no engagement; nothing to show. Open one with "
            f"`fin engagement open {company} --periods FY24,FY25,FY26`"
        )
    return rows[0]


def bridge(
    run: Run, company: str, period: str | None = None, scope: str | None = None
) -> dict[str, Any]:
    """The latest engagement's adjusted EBITDA, bridge steps and walk, by period and scope (only
    `period` and `scope` when given), with the document unit for display. E803 without an
    engagement."""
    where = "WHERE (? IS NULL OR period = ?) AND (? IS NULL OR scope = ?)"
    params = [period, period, scope, scope]
    with company_database(run, company) as con:
        engagement = latest_engagement(con, company)
        return {
            "company": company,
            "engagement": engagement,
            "unit": document_unit(con),
            "adjusted_ebitda": fetch_dicts(
                con, f"SELECT * FROM adjusted_ebitda {where} ORDER BY period, scope", params
            ),
            "bridge": fetch_dicts(
                con, f"SELECT * FROM ebitda_bridge {where} ORDER BY period, scope, step", params
            ),
            "walk": fetch_dicts(
                con, f"SELECT * FROM ebitda_to_cash {where} ORDER BY period, scope", params
            ),
        }


def missing(row: dict[str, Any], inputs: tuple[str, ...]) -> str:
    """'  (missing: cf_cash_generated)' for the inputs without a value, or ''."""
    names = [ACCOUNTS.get(name, name) for name in inputs if row[name] is None]
    return f"  (missing: {', '.join(names)})" if names else ""


def _line(label: str, value: Any, unit: str, note: str = "") -> str:
    shown = "-" if value is None else amount(in_unit(value, unit))
    return f"  {label:<56} {shown:>16}{note}"


def _ratio(label: str, value: Any, note: str) -> str:
    shown = "-" if value is None else f"{value:.2%}"
    return f"  {label:<56} {shown:>16}{note}"


def format_bridge(result: dict[str, Any]) -> str:
    """Plain text: the engagement, then per period and scope the bridge steps (accepted
    adjustments only; proposed ones are named, not applied) and the EBITDA-to-cash walk."""
    company, unit = result["company"], result["unit"]
    out = [f"{company}: {format_engagement(result['engagement'])}"]
    if not result["adjusted_ebitda"]:
        out.append("no periods to show")
        return "\n".join(out)
    walks = {(w["period"], w["scope"]): w for w in result["walk"]}
    for summary in result["adjusted_ebitda"]:
        key = (summary["period"], summary["scope"])
        out += ["", f"{company} {key[1]} {key[0]}: EBITDA bridge ({unit_title(unit)})"]
        for step in (s for s in result["bridge"] if (s["period"], s["scope"]) == key):
            label = step["description"]
            if step["kind"] == "adjustment":
                label = f"#{step['adj_id']} {step['category']}: {label}"
            note = missing(summary, ("reported_ebitda",)) if step["amount"] is None else ""
            out.append(_line(label, step["amount"], unit, note))
        if summary["proposed_count"]:
            out.append(
                f"  not applied: {summary['proposed_count']} proposed, "
                f"{amount(in_unit(summary['proposed_adjustments'], unit))}"
            )
        walk = walks[key]
        out.append(f"{company} {key[1]} {key[0]}: EBITDA to cash ({unit_title(unit)})")
        for name, label, inputs in WALK_LINES:
            note = missing(walk, inputs) if walk[name] is None else ""
            out.append(_line(label, walk[name], unit, note))
        for name, label, inputs in WALK_RATIOS:
            note = missing(walk, inputs) if walk[name] is None else ""
            out.append(_ratio(label, walk[name], note))
    return "\n".join(line.rstrip() for line in out)
