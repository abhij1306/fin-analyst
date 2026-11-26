"""What the Home screen shows, from `fin status` data: counts, the next gate and what waits for the
user. Plain data for the templates; every number comes from `fin.status`."""

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


def home(status: dict[str, Any]) -> dict[str, Any]:
    gate = next_gate(status)
    cards = [
        (status["pending_mappings"], "mappings waiting for you"),
        (status["open_findings"], "open findings on filed data"),
        (status["accepted_findings"], "accepted findings"),
        (gate[0] if gate else "-", f"next gate ({gate[1]})" if gate else "no gate waiting"),
    ]
    return {
        "status": status,
        "periods": periods_line(status),
        "cards": cards,
        "waiting": waiting(status),
    }
