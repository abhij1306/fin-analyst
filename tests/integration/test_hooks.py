"""Run the Claude Code hooks the way Claude Code does: JSON on stdin, check exit code and output."""

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


def asked_reason(result: subprocess.CompletedProcess) -> str:
    """Assert the hook asked the user to confirm (exit 0, ask JSON on stdout); return the reason."""
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert (output["hookEventName"], output["permissionDecision"]) == ("PreToolUse", "ask")
    reason = output["permissionDecisionReason"]
    assert reason.startswith("E701: ")
    assert reason.endswith(". Approve only if you asked for this change.")
    return reason


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("rm -rf ~/FinAnalysis/companies/acme.duckdb", "`rm` on the data folder"),
        ("rm -rf $HOME/FinAnalysis", "`rm` on the data folder"),
        ("cd ~/FinAnalysis && rm -f notes.md", "`rm` on the data folder"),
        ("mv ~/FinAnalysis/inbox/a.xlsx /tmp/", "`mv` on the data folder"),
        ("find ~/FinAnalysis -name '*.tmp' -delete", "`find -delete` on the data folder"),
        ("echo x > ~/FinAnalysis/review/x.json", "redirecting output into the data folder"),
        ("ls | tee -a ~/FinAnalysis/logs/x.log", "`tee` into the data folder"),
        ("duckdb ~/FinAnalysis/companies/acme.duckdb", "database opened outside `fin`"),
        (
            "python -c \"import duckdb; duckdb.connect('~/FinAnalysis/companies/a.duckdb')\"",
            "database opened outside `fin`",
        ),
        ("uv run fin delete acme", "E701: `fin delete` changes data."),
        ("fin review confirm 12", "E701: `fin review confirm` changes data."),
        (
            "uv run fin review confirm 3 --company bluepeak --account trade_receivables",
            "E701: `fin review confirm` changes data.",
        ),
        ("uv run --frozen fin review confirm --all", "E701: `fin review confirm` changes data."),
        ("bash -c 'rm -rf ~/FinAnalysis/shared'", "`rm` on the data folder"),
        (
            'uv run fin sql kestrel --write --sql "UPDATE facts SET value = 1 WHERE fact_id = 3"',
            "E701: `fin sql --write` changes data.",
        ),
    ],
)
def test_guard_asks(command: str, reason: str, home: Path, env: dict[str, str]) -> None:
    result = run_hook("guard.py", bash(command, home / "work"), env)
    assert reason in asked_reason(result)
    [entry] = read_log(home)
    assert (entry["tool"], entry["command"], entry["decision"], entry["code"]) == (
        "Bash",
        command,
        "ask",
        "E701",
    )
    assert "blocked" not in entry


@pytest.mark.parametrize(
    "command",
    [
        "uv run fin doctor",
        "uv run fin query kestrel --question 'Revenue?' --sql 'SELECT 1'",
        "uv run fin sql kestrel --sql 'SELECT 1'",
        "uv run fin ingest acme ~/FinAnalysis/inbox/acme",
        "rm -rf build/tmp",
        "ls ~/FinAnalysis/companies",
        "cat ~/FinAnalysis/notes/acme/auditor.md",
        "git commit -m 'document fin delete'",
        "echo done > out.txt",
    ],
)
def test_guard_allows(command: str, home: Path, env: dict[str, str]) -> None:
    result = run_hook("guard.py", bash(command, home / "work"), env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (home / "FinAnalysis" / "logs" / "claude_actions.jsonl").exists()


def test_guard_uses_fin_data_dir_override(tmp_path: Path, home: Path, env: dict[str, str]) -> None:
    custom = tmp_path / "custom-data"
    env["FIN_DATA_DIR"] = str(custom)
    asked = run_hook("guard.py", bash(f"rm -r {custom.as_posix()}/inbox", home / "work"), env)
    allowed = run_hook("guard.py", bash("rm -r ~/FinAnalysis/inbox", home / "work"), env)
    assert "`rm` on the data folder" in asked_reason(asked)
    assert (allowed.returncode, allowed.stdout) == (0, "")


def test_guard_asks_before_read_tool_on_database(home: Path, env: dict[str, str]) -> None:
    db = home / "FinAnalysis" / "companies" / "acme.duckdb"
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(db)}, "cwd": str(home)}
    asked = run_hook("guard.py", payload, env)
    payload["tool_input"]["file_path"] = str(home / "FinAnalysis" / "notes" / "acme.md")
    allowed = run_hook("guard.py", payload, env)
    assert "acme.duckdb is a data-folder database opened outside `fin`" in asked_reason(asked)
    assert (allowed.returncode, allowed.stdout) == (0, "")


def test_guard_asks_on_bad_input(home: Path, env: dict[str, str]) -> None:
    result = run_hook("guard.py", "not json", env)
    assert asked_reason(result).startswith("E701: guard could not read hook input")
    [entry] = read_log(home)
    assert (entry["decision"], entry["code"]) == ("ask", "E701")


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
