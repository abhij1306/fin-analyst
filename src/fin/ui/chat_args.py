"""What the UI chat tells `claude -p` to do (docs/web-ui-plan.md, "Chat"; decisions/web-ui.md 9).

The chat runs the installed Claude Code in the repo folder, with the user's own login, hooks on,
and never with bypassed permissions. The user's decisions are denied to Claude here (the buttons of
the screens are the only way to take them); every other permission request is shown in the chat.
"""

import os
import re
import shlex
import shutil

from fin.audit.errors import ChatUnavailable, UiActionInvalid

# The commands that are the user's decisions (CLAUDE.md, "Diligence"; the screens' buttons).
DECISION_COMMANDS = [
    ("gate", "approve"),
    ("fdd", "finding", "final"),  # also its --undo, the return to draft
    ("query", "approve"),
    ("query", "sent"),
    ("query", "close"),
    ("query", "withdraw"),
    ("adjust", "accept"),
    ("adjust", "reject"),
    ("review", "confirm"),
    ("review", "reject"),
    ("findings", "accept"),
    ("provisions", "confirm"),
    ("provisions", "reject"),
    ("netdebt", "item", "accept"),
    ("netdebt", "item", "reject"),
    ("reference", "confirm"),
    ("reference", "reject"),
    ("dataroom", "approve"),
]
FIN_INVOCATIONS = ["fin", "uv run fin", "uv run python -m fin", "python -m fin"]
GUIDANCE = (
    "You are running inside the fin web UI. The user's decisions (approve a gate, mark an FDD "
    "finding final or back to draft, approve/mark sent/close/withdraw a query, accept or reject an "
    "adjustment, confirm or reject a mapping or a reference line, approve a data-room file, "
    "accept a finding on filed data) are buttons on the UI screens: you cannot run those commands "
    "here. When one is due, say which screen has the button."
)
DECISION_RE = re.compile(
    r"\bfin\s+(?:" + "|".join(r"\s+".join(map(re.escape, c)) for c in DECISION_COMMANDS) + r")\b"
)
RULE_SAFE = re.compile(r"[^*()\\\n]+")  # a command or path that can be allowed as an exact rule
ONCE_TOOLS = {"Bash": "command", "Edit": "file_path", "Write": "file_path", "Read": "file_path"}
ONCE_RULE = re.compile(r"(?:Bash|Edit|Write|Read)\([^*()\\\n]+\)")  # what once_rule makes


def disallowed_tools() -> list[str]:
    """The `--disallowedTools` rules: every decision command under each way of calling fin."""
    return [
        f"Bash({prefix} {' '.join(command)} *)"
        for prefix in FIN_INVOCATIONS
        for command in DECISION_COMMANDS
    ]


def is_decision(command: str) -> bool:
    """Whether a shell command is one of the user's decisions."""
    try:
        words = " ".join(shlex.split(command, posix=True))
    except ValueError:  # an unbalanced quote: look at the raw text
        words = command
    return DECISION_RE.search(words) is not None


def once_rule(tool: str, tool_input: dict[str, object]) -> str | None:
    """The `--allowedTools` rule that allows exactly this one call (Allow once), or None when it
    cannot be named exactly (other tools, or a value with a wildcard, bracket or backslash)."""
    key = ONCE_TOOLS.get(tool)
    value = tool_input.get(key) if key else None
    if not isinstance(value, str) or not RULE_SAFE.fullmatch(value):
        return None
    return f"{tool}({value})"


def checked_rule(rule: str) -> str:
    """A stored Allow once rule, only if it has the shape once_rule makes (E008 otherwise): the
    conversation file must not be able to hand claude anything else."""
    if not ONCE_RULE.fullmatch(rule):
        raise UiActionInvalid(f"not a rule the chat can allow: {rule!r}")
    return rule


def locate() -> list[str]:
    """The command that starts Claude Code; E009 when it is not installed."""
    found = shutil.which("claude")
    if not found:
        raise ChatUnavailable(
            "Claude Code is not installed (no `claude` on the path): install it from "
            "https://claude.com/claude-code and log in"
        )
    return [found]


def api_key_set() -> bool:
    """ANTHROPIC_API_KEY would make Claude Code bill the API instead of the user's login."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def turn_argv(
    command: list[str], session: str, started: bool, once: str | None = None
) -> list[str]:
    """The argv of one turn. The prompt goes in on stdin. Never --bare (hooks and CLAUDE.md apply)
    and never a bypass mode: a request that is not allowed is denied, and the chat shows it."""
    argv = [
        *command,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "default",
        "--resume" if started else "--session-id",
        session,
        "--disallowedTools",
        ",".join(disallowed_tools()),
        "--append-system-prompt",
        GUIDANCE,
    ]
    return [*argv, f"--allowedTools={checked_rule(once)}"] if once else argv
