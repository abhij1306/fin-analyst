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


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("rm -rf ~/FinAnalysis/companies/acme.duckdb", "`rm` on the data folder"),
        ("rm -rf $HOME/FinAnalysis", "`rm` on the data folder"),
        ("cd ~/FinAnalysis && rm -f notes.md", "`rm` on the data folder"),
        ("mv ~/FinAnalysis/inbox/a.xlsx /tmp/", "`mv` on the data folder"),
        ("find ~/FinAnalysis -name '*.tmp' -delete", "`find -delete` on the data folder"),
        ("echo x > ~/FinAnalysis/review/x.json", "redirecting output into the data folder"),
        ("ls | tee -a ~/FinAnalysis/logs/x.log", "with tee is not allowed"),
        ("duckdb ~/FinAnalysis/companies/acme.duckdb", "is a database in the data folder"),
        (
            "python -c \"import duckdb; duckdb.connect('~/FinAnalysis/companies/a.duckdb')\"",
            "is a database in the data folder",
        ),
        ("uv run fin delete acme", "`fin delete` is human-only"),
        ("fin review confirm 12", "`fin review confirm` is human-only"),
        ("uv run --frozen fin review confirm --all", "`fin review confirm` is human-only"),
        ("bash -c 'rm -rf ~/FinAnalysis/shared'", "`rm` on the data folder"),
    ],
)
def test_guard_blocks(command: str, reason: str, home: Path, env: dict[str, str]) -> None:
    result = run_hook("guard.py", bash(command, home / "work"), env)
    assert result.returncode == 2
    assert result.stderr.startswith("E701: blocked by guard: ")
    assert reason in result.stderr
    [entry] = read_log(home)
    assert (entry["tool"], entry["command"], entry["blocked"], entry["code"]) == (
        "Bash",
        command,
        True,
        "E701",
    )


@pytest.mark.parametrize(
    "command",
    [
        "uv run fin doctor",
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
    assert (result.returncode, result.stderr) == (0, "")


def test_guard_uses_fin_data_dir_override(tmp_path: Path, home: Path, env: dict[str, str]) -> None:
    custom = tmp_path / "custom-data"
    env["FIN_DATA_DIR"] = str(custom)
    blocked = run_hook("guard.py", bash(f"rm -r {custom.as_posix()}/inbox", home / "work"), env)
    allowed = run_hook("guard.py", bash("rm -r ~/FinAnalysis/inbox", home / "work"), env)
    assert (blocked.returncode, allowed.returncode) == (2, 0)


def test_guard_blocks_read_tool_on_database(home: Path, env: dict[str, str]) -> None:
    db = home / "FinAnalysis" / "companies" / "acme.duckdb"
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(db)}, "cwd": str(home)}
    blocked = run_hook("guard.py", payload, env)
    payload["tool_input"]["file_path"] = str(home / "FinAnalysis" / "notes" / "acme.md")
    allowed = run_hook("guard.py", payload, env)
    assert blocked.returncode == 2
    assert "acme.duckdb is a database in the data folder" in blocked.stderr
    assert allowed.returncode == 0


def test_guard_fails_closed_on_bad_input(env: dict[str, str]) -> None:
    result = run_hook("guard.py", "not json", env)
    assert result.returncode == 2
    assert result.stderr.startswith("E701: guard could not read hook input")


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
