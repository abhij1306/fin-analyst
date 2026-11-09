"""PreToolUse hook (Bash): ask the user before any command that looks like a gate approval.

Only the user approves a diligence gate (CLAUDE.md, "Diligence"). Reads the hook payload (JSON)
from stdin. The command is normalised first (quotes and backslashes removed, whitespace collapsed,
lower case), so `fin "gate" approve` or a line break between the words still counts. It looks like
a gate approval, and the hook prints a decision of "ask" so Claude Code asks the user first, when
it has the word `gate` followed later by `approve`, or names `gate_approvals`, `approve_in`,
`gates.approve` or `from fin.diligence.gates import`. When in doubt it asks: a commit message with
"gate approve" in it asks too. For anything else, and for input it cannot read, it prints nothing
and exits 0, so no other command is ever blocked. `fin sql --write` refuses to write the gate
approvals table on its own (E806). See PLAN.md §8.
"""

import json
import re
import sys

GATE = re.compile(r"\bgate\b")
APPROVE = re.compile(r"\bapprove\b")
NAMES = ("gate_approvals", "approve_in", "gates.approve", "from fin.diligence.gates import")


def normalise(command: str) -> str:
    """The command as the shell would mostly see it: no quotes, no backslashes (a backslash-newline
    joins lines), whitespace collapsed to single spaces, lower case."""
    text = command.replace("\\\n", " ")
    text = re.sub(r"[\"'`\\]", "", text)
    return " ".join(text.split()).lower()


def looks_like_approval(command: str) -> bool:
    """The word `gate` followed later by `approve`, or one of NAMES, in the normalised command."""
    text = normalise(command)
    gate = GATE.search(text)
    if gate is not None and APPROVE.search(text, gate.end()):
        return True
    return any(name in text for name in NAMES)


def decision(payload: object) -> dict[str, object] | None:
    """The hook output for a payload: an "ask" decision for a command that looks like a gate
    approval, else None."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not looks_like_approval(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"Only you approve a diligence gate: {command}",
        }
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    output = decision(payload)
    if output is not None:
        sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
