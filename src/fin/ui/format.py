"""How the UI shows values: one timestamp format, status tones and grouped log problems. Plain
functions registered as Jinja filters (`register`); no data access."""

import re
from collections import Counter
from datetime import datetime
from typing import Any

from jinja2 import Environment
from markupsafe import Markup

from fin.output import escape

OK = {"loaded", "approved", "final", "accepted", "confirmed", "closed", "ok", "done", "success",
      "passed", "complete"}  # fmt: skip
WARN = {"open", "blocked", "held", "answered", "partial", "pending", "medium", "running",
        "warning", "queued"}  # fmt: skip
BAD = {"failed", "failures", "error", "rejected", "high", "refused", "interrupted"}


def when(value: Any) -> str:
    """A stored timestamp as '26 Nov 2026, 13:18' (no seconds). Text that is not a timestamp is
    shown as it is, so the screen never hides what is stored."""
    if value is None or value == "":
        return ""
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{moment.day} {moment:%b %Y, %H:%M}"


def tone(status: Any) -> str:
    """The badge tone of a status word: ok, warn, bad or neutral."""
    word = str(status or "").lower()
    if word in OK:
        return "ok"
    if word in WARN:
        return "warn"
    if word in BAD:
        return "bad"
    return "neutral"


def problems_view(problems: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The problems of a run as a badge label ('2 errors, 11 warnings'), its tone and the distinct
    messages with how often each came up. None when there are none."""
    if not problems:
        return None
    levels = Counter(p["level"] for p in problems)
    parts = [f"{n} {level}{'' if n == 1 else 's'}" for level, n in sorted(levels.items())]
    groups = Counter((p.get("error_code") or p["level"], p["message"]) for p in problems)
    return {
        "label": ", ".join(parts),
        "tone": "bad" if levels.get("error") else "warn",
        "groups": [
            {"code": code, "message": message, "count": n} for (code, message), n in groups.items()
        ],
    }


def code(text: Any) -> Markup:
    """Text with its `backticked` commands shown as code; everything else is escaped."""
    escaped = escape.html(text or "")  # Markup below is safe: the text is escaped first
    return Markup(re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped))  # noqa: S704


def gate_names(lines: list[str]) -> list[str]:
    """The gates a refusal names ('... needs gate G2 (Reconciliation) approved; gate G1 ...'), each
    once, in order: an E801 alert links to them on the Gates screen."""
    return list(dict.fromkeys(re.findall(r"\bgate (G\d)\b", " ".join(lines))))


def register(env: Environment) -> None:
    env.filters.update(
        when=when, tone=tone, problems_view=problems_view, code=code, gate_names=gate_names
    )
