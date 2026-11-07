"""Run the Claude Code hook the way Claude Code does: JSON on stdin, check exit code and output."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def run_hook(name: str, payload: dict | str, env: dict[str, str]) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / "FinAnalysis" / "companies").mkdir(parents=True)
    (path / "work").mkdir()
    return path.resolve()


@pytest.fixture
def env(home: Path) -> dict[str, str]:
    """Default data folder (~/FinAnalysis) with HOME pointed at a temp folder."""
    environ = {k: v for k, v in os.environ.items() if k != "FIN_DATA_DIR"}
    environ.update(HOME=str(home), USERPROFILE=str(home))
    return environ


def bash(command: str, cwd: Path) -> dict:
    return {
        "session_id": "sess-1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }


def read_log(home: Path) -> list[dict]:
    lines = (home / "FinAnalysis" / "logs" / "claude_actions.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in lines.splitlines()]


def test_log_action_appends_tool_call(home: Path, env: dict[str, str]) -> None:
    first = bash("uv run fin doctor", home / "work") | {"hook_event_name": "PostToolUse"}
    second = {
        "session_id": "sess-1",
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/a.md", "content": "x" * 5000},
        "cwd": str(home),
    }
    assert run_hook("log_action.py", first, env).returncode == 0
    assert run_hook("log_action.py", second, env).returncode == 0
    bash_entry, write_entry = read_log(home)
    assert {k: bash_entry[k] for k in ("session_id", "event", "tool", "command")} == {
        "session_id": "sess-1",
        "event": "PostToolUse",
        "tool": "Bash",
        "command": "uv run fin doctor",
    }
    assert write_entry["tool"] == "Write"
    assert write_entry["command"]["file_path"] == "/tmp/a.md"
    assert write_entry["command"]["content"].endswith("... [truncated, 5000 chars]")
