"""PreToolUse hook (Bash): ask the user before any command that runs `fin gate approve`.

Only the user approves a diligence gate (CLAUDE.md, "Diligence"). Reads the hook payload (JSON)
from stdin. For a Bash command running `fin gate approve`, it prints a decision of "ask", so Claude
Code asks the user first; for anything else, and for input it cannot read, it prints nothing and
exits 0, so no other command is ever blocked. See PLAN.md §8.
"""

import json
import re
import sys

APPROVE = re.compile(r"\bfin\s+gate\s+approve\b")


def decision(payload: object) -> dict[str, object] | None:
    """The hook output for a payload: an "ask" decision for `fin gate approve`, else None."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not APPROVE.search(command):
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
