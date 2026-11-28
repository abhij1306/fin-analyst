"""What the Home screen shows, from `fin status` data: counts, the next gate and what waits for the
user. Plain data for the templates; every number comes from `fin.status`."""

import re
from typing import Any

# Query statuses with a button for the user: approve a draft, mark an approved one sent,
# close an answered one.
QUERY_ACTIONABLE = ("draft", "approved", "answered")


def periods_line(status: dict[str, Any]) -> str:
    periods = status["periods"]
    loaded = [p for kind in ("years", "halves", "quarters", "nine_months") for p in periods[kind]]
    return ", ".join(loaded) if loaded else "nothing loaded yet"


def waiting(status: dict[str, Any]) -> list[dict[str, str]]:
    """The things that wait for the user, each with the screen where it is decided."""
    diligence = status["diligence"] or {}
    candidates = [
        (status["pending_mappings"], "mappings to review", "Mappings"),
        (diligence.get("queries", {}).get("draft", 0), "draft queries to approve", "Queries"),
        (diligence.get("proposed_adjustments", 0), "proposed adjustments to decide", "Queries"),
        (status["open_findings"], "open findings on filed data", "Files"),
        (len(status["inbox_not_ingested"]), "files in the inbox not loaded yet", "Files"),
    ]
    return [{"text": f"{n} {text}", "where": where} for n, text, where in candidates if n]


def next_gate(status: dict[str, Any]) -> tuple[str, str] | None:
    """The first gate that is not approved, as (name, state)."""
    gates = (status["diligence"] or {}).get("gates", {})
    return next(((name, state) for name, state in gates.items() if state != "approved"), None)


def tab_counts(status: dict[str, Any]) -> dict[str, int]:
    """The number on a tab: what waits for the user there. Empty tabs are left out."""
    diligence = status["diligence"] or {}
    gates = diligence.get("gates", {})
    counts = {
        "files": status["open_findings"] + len(status["inbox_not_ingested"]),
        "mappings": status["pending_mappings"],
        "queries": sum(diligence.get("queries", {}).get(s, 0) for s in QUERY_ACTIONABLE)
        + diligence.get("proposed_adjustments", 0),
        "gates": sum(1 for state in gates.values() if state != "approved"),
    }
    return {screen: n for screen, n in counts.items() if n}


# The command a failing gate line names -> the screen where the user does it, and the link's text.
# A line naming another command (or none) is offered to Claude in chat instead.
GATE_FIXES = (
    ("fin review list", "mappings", "Review the mappings"),
    ("fin findings", "files", "See the findings on the Files screen"),
    ("fin ingest", "files", "Load the files on the Files screen"),
    ("fin query", "queries", "Open the queries"),
    ("fin adjust", "queries", "Open the adjustments"),
    ("fin fdd finding", "findings", "Open the FDD findings"),
)
COMMAND = re.compile(r"`(fin [^`]*)`")


def gate_fix(text: str) -> dict[str, str] | None:
    """Where the user closes a failing checklist line: {"screen", "label"} from the first command
    the line names, or None (then the screen offers to ask Claude). A review of another kind
    (`--kind`) is not on the Mappings screen."""
    for command in COMMAND.findall(text):
        if "--kind" in command:
            return None
        for prefix, screen, label in GATE_FIXES:
            if command.startswith(prefix):
                return {"screen": screen, "label": label}
    return None


def home(status: dict[str, Any]) -> dict[str, Any]:
    gate = next_gate(status)
    cards = [
        (status["pending_mappings"], "mappings waiting for you"),
        (status["open_findings"], "open findings on filed data"),
        (status["accepted_findings"], "accepted findings"),
        (status["stated_figures"], "figures you stated, not from a document"),
        (status["corrected_figures"], "figures corrected after fin misread them"),
        (gate[0] if gate else "-", f"next gate ({gate[1]})" if gate else "no gate waiting"),
    ]
    return {
        "status": status,
        "periods": periods_line(status),
        "cards": cards,
        "waiting": waiting(status),
        "not_run": status["checks_not_run"],
    }
