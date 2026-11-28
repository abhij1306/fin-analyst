"""PreToolUse hook (Bash): ask the user before any command that looks like a gate approval or
marks an FDD finding final.

Only the user approves a diligence gate or marks an FDD finding final (CLAUDE.md, "Diligence").
Reads the hook payload (JSON) from stdin. The command is normalised first (quotes and backslashes
removed, whitespace collapsed, lower case), so `fin "gate" approve` or a line break between the
words still counts. It looks like a gate approval, and the hook prints a decision of "ask" so
Claude Code asks the user first, when it has the word `gate` followed later by `approve`, or names
`gate_approvals`, `approve_in`, `gates.approve`, `from fin.diligence.gates import` or
`approve_gate`; it marks a finding final when it has `finding final` (`fin fdd finding final`,
with or without --undo) or names `findings.set_status`, `fin.fdd.findings`, `from fin.fdd import
findings` (an import of it, aliased or not) or `mark_fdd_findings`. It also asks when the raw
command names fin with `gate` or `finding` and has a `$` or backtick expansion
(`a=approve; fin gate $a`), since the words the shell runs are not in the text. When in doubt
it asks: a commit message with "gate approve" in it asks too. For anything else, and for input it
cannot read, it prints nothing and exits 0, so no other command is ever blocked. `fin sql
--write` refuses to write the gate approvals and FDD findings tables on its own (E806). See
docs/product.md §8.
"""

import json
import re
import sys

GATE = re.compile(r"\bgate\b")
APPROVE = re.compile(r"\bapprove\b")
NAMES = (
    "gate_approvals",
    "approve_in",
    "gates.approve",
    "from fin.diligence.gates import",
    "approve_gate",
)
FINAL = re.compile(r"\bfinding final\b")
FINAL_NAMES = (
    "findings.set_status",
    "fin.fdd.findings",
    "from fin.fdd import findings",
    "mark_fdd_findings",
)
# fin with gate or finding and a shell expansion: the words the shell runs are not in the text
# (`a=approve; fin gate $a`), so it asks.
FIN = re.compile(r"\bfin\b")
GATE_OR_FINDING = re.compile(r"\b(?:gate|finding)\b")
EXPANSION = re.compile(r"[$`]")


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


def looks_like_final(command: str) -> bool:
    """`finding final`, or one of FINAL_NAMES, in the normalised command."""
    text = normalise(command)
    return FINAL.search(text) is not None or any(name in text for name in FINAL_NAMES)


def hides_words(command: str) -> bool:
    """fin with `gate` or `finding` and a `$` or backtick expansion, in the raw command."""
    text = command.lower()
    return bool(FIN.search(text) and GATE_OR_FINDING.search(text) and EXPANSION.search(text))


def decision(payload: object) -> dict[str, object] | None:
    """The hook output for a payload: an "ask" decision for a command that looks like a gate
    approval or marks an FDD finding final, else None."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    if looks_like_approval(command):
        reason = f"Only you approve a diligence gate: {command}"
    elif looks_like_final(command):
        reason = f"Only you mark an FDD finding final: {command}"
    elif hides_words(command):
        reason = (
            "This fin command expands a shell variable or command, so it may approve a gate or "
            f"mark an FDD finding final: {command}"
        )
    else:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
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
