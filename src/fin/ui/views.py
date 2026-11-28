"""What the Home screen shows, from `fin status` data: counts, the next gate and what waits for the
user. Plain data for the templates; every number comes from `fin.status`."""

import re
from typing import Any

from fin.status import WAITING

# The screen where each kind of waiting item (fin.status.WAITING) is decided.
WHERE = {
    **dict.fromkeys(
        ("mappings", "ledgers", "customers", "tax", "related", "netdebt", "matches"), "Mappings"
    ),
    "reference_lines": "References",
    **dict.fromkeys(("open_findings", "inbox", "dataroom"), "Files"),
    **dict.fromkeys(("queries", "adjustments"), "Queries"),
    **dict.fromkeys(("fdd_drafts", "provisions", "netdebt_items"), "Findings"),
}


def periods_line(status: dict[str, Any]) -> str:
    periods = status["periods"]
    loaded = [p for kind in ("years", "halves", "quarters", "nine_months") for p in periods[kind]]
    return ", ".join(loaded) if loaded else "nothing loaded yet"


def waiting(status: dict[str, Any]) -> list[dict[str, str]]:
    """The things that wait for the user (`fin status`'s counts), each with the screen where it is
    decided."""
    return [
        {"text": f"{n} {WAITING[key]}", "where": WHERE[key]}
        for key, n in status["waiting"].items()
        if n
    ]


def next_gate(status: dict[str, Any]) -> tuple[str, str] | None:
    """The first gate that is not approved, as (name, state)."""
    gates = (status["diligence"] or {}).get("gates", {})
    return next(((name, state) for name, state in gates.items() if state != "approved"), None)


def tab_counts(status: dict[str, Any]) -> dict[str, int]:
    """The number on a tab: what waits for the user there (`fin status`'s counts, summed per
    screen), and the gates not approved. Empty tabs are left out."""
    gates = (status["diligence"] or {}).get("gates", {})
    counts = {"gates": sum(1 for state in gates.values() if state != "approved")}
    for key, n in status["waiting"].items():
        screen = WHERE[key].lower()
        counts[screen] = counts.get(screen, 0) + n
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
    ("fin provisions", "findings", "Open the provision ledgers"),
    ("fin netdebt item", "findings", "Open the net debt items"),
)
COMMAND = re.compile(r"`(fin [^`]*)`")


def gate_fix(text: str) -> dict[str, str] | None:
    """Where the user closes a failing checklist line: {"screen", "label"} from the first command
    the line names, or None (then the screen offers to ask Claude). A review with an option
    (`--netdebt`, `--tax`, `--related`, ...) is another kind, not on the Mappings screen."""
    for command in COMMAND.findall(text):
        if command.startswith("fin review") and "--" in command:
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
