"""The PreToolUse hook gate_guard.py, run the way Claude Code runs it: JSON on stdin."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "gate_guard.py"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def bash(command: str) -> str:
    return json.dumps(
        {
            "session_id": "sess-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


def test_approving_a_gate_asks_the_user() -> None:
    result = run_hook(bash("uv run fin gate approve bluepeak G2"))

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                "Only you approve a diligence gate: uv run fin gate approve bluepeak G2"
            ),
        }
    }


@pytest.mark.parametrize(
    "command",
    [
        "uv run fin gate status bluepeak",
        "uv run fin adjust accept bluepeak 1",
        "echo fin gate approved",
        "ls",
    ],
)
def test_other_commands_pass_without_output(command: str) -> None:
    result = run_hook(bash(command))

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "",
        "[1, 2]",
        json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}),
        json.dumps({"tool_input": {"command": 7}}),
        json.dumps({"tool_input": "fin gate approve bluepeak G1"}),
    ],
)
def test_malformed_input_passes_without_output(stdin: str) -> None:
    result = run_hook(stdin)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
