"""The Differences screen (SPEC-15): every difference of the latest reconciliations as a tree,
from the headline (trial balance against statements) down to the invoice, each with what its
children explain and what is left unexplained, its query and its sources. Unit picker and "over
the threshold only". Read-only: the rows the user ticks go to the assistant's chat as a request
to draft one query (`fin query raise --items`); the user approves it on the Queries screen.
Every number is SQL's (`fin.discrepancies.rows`)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fin.audit.errors import EngagementState, UiActionInvalid
from fin.config import DISPLAY_UNITS, Display
from fin.discrepancies.rows import amount, discrepancy_rows, query_text, tree
from fin.display import display_settings
from fin.paths import company_db_path

MAX_ASK = 50  # the most rows one request to the assistant names
MONEY = ("left_amount", "right_amount", "difference", "explained", "unexplained")


def _shown(row: dict[str, Any], display: Display) -> dict[str, Any]:
    return {
        **row,
        **{f"{c}_text": amount(row, c, display) for c in MONEY},
        "query_text": query_text(row),
    }


def _over(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The nodes over the threshold, or with one below them, with their children pruned alike."""
    kept = []
    for node in nodes:
        children = _over(node["children"])
        if node["row"]["over_threshold"] or children:
            kept.append({**node, "children": children})
    return kept


def differences_screen(
    root: Path, company: str, query: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The screen's data for the pickers in `query` (unit, over), or why there is none (nothing
    loaded, no open engagement). E001 for a unit not offered."""
    query = query or {}
    display = display_settings(root, company, query.get("unit") or None)
    over = query.get("over") == "1"
    base = {"unit": display.unit, "units": tuple(DISPLAY_UNITS), "over": over}
    if not company_db_path(root, company).is_file():
        return base | {"no_engagement": f"Nothing is loaded for {company} yet."}
    try:
        result = discrepancy_rows(root, company, display=display)
    except EngagementState as error:
        return base | {"no_engagement": f"{error.code}: {error.message}"}
    nodes = tree([_shown(row, display) for row in result["rows"]])
    return base | {
        "nodes": _over(nodes) if over else nodes,
        "count": len(result["rows"]),
        "unit_label": display.unit_label,
    }


def query_prompt(root: Path, company: str, items: list[str]) -> str:
    """The chat message that asks the assistant to draft one query for the ticked rows. E008 when
    none is ticked, an id is not a row that can be queried, or the rows are of several periods."""
    if not 0 < len(items) <= MAX_ASK or not all(i.isascii() and i.isdigit() for i in items):
        raise UiActionInvalid(f"tick from 1 to {MAX_ASK} rows that can be queried")
    ids = list(dict.fromkeys(int(i) for i in items))
    rows = {r["item_id"]: r for r in discrepancy_rows(root, company)["rows"]}
    bad = [i for i in ids if i not in rows or not rows[i]["queryable"]]
    if bad:
        raise UiActionInvalid(
            f"rows {', '.join(f'#{i}' for i in bad)} cannot be queried: only a difference at or "
            "above the threshold that is in no query yet can be"
        )
    periods = sorted({rows[i]["period"] for i in ids})
    if len(periods) > 1:
        raise UiActionInvalid(
            f"the rows are of several periods ({', '.join(periods)}): one query per period"
        )
    listed = ",".join(map(str, ids))
    return (
        f"Please draft one accountant query for {company} about the differences #"
        f"{', #'.join(map(str, ids))} ({periods[0]}). Read them with `uv run fin discrepancies "
        f"{company} --json`, word the question neutrally (what differs, both sources, both "
        f"amounts), and raise it with `uv run fin query raise {company} --items {listed} "
        '--question "..."`. Do not approve or send it: I review it on the Queries screen.'
    )
