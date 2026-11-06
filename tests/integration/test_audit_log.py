"""Every command gets a run_id, `runs` start/end rows, `events`, a JSONL log and a summary (PLAN.md
§8)."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from fin.audit.errors import GuardBlocked
from fin.audit.logger import Run, execute
from fin.cli import app
from fin.db.conn import connect

runner = CliRunner()

Query = Callable[..., list[tuple[Any, ...]]]


def run_id_of(stderr: str) -> str:
    match = re.search(
        r"^summary: loaded \d+, skipped \d+, failed \d+, pending review \d+, run_id (\S+)$",
        stderr,
        re.MULTILINE,
    )
    assert match is not None, stderr
    return match.group(1)


def jsonl_records(root: Path, run_id: str) -> list[dict[str, Any]]:
    [path] = (root / "logs").glob(f"*/{run_id}.jsonl")
    assert path.parent.name == f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _all_tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"uv": "uv 0.11.28", "node": "v24.11.1", "quarto": "1.8.25"}
    monkeypatch.setattr("fin.doctor.tool_version", versions.get)
    monkeypatch.setattr("fin.doctor.sys.version_info", (3, 12, 13, "final", 0))


def test_doctor_writes_runs_events_and_jsonl(
    data_root: Path, audit_query: Query, monkeypatch: pytest.MonkeyPatch
) -> None:
    _all_tools_present(monkeypatch)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    run_id = run_id_of(result.stderr)
    assert result.stderr == (
        f"summary: loaded 0, skipped 0, failed 0, pending review 0, run_id {run_id}\n"
    )

    runs = audit_query(
        "SELECT record, command, args, status, exit_code, failed, ended_at IS NULL, "
        "fin_version, duckdb_version, python_version, git_commit "
        "FROM runs WHERE run_id = ? ORDER BY record DESC",
        [run_id],
    )
    assert [row[:7] for row in runs] == [
        ("start", "doctor", '{"json": false}', "started", None, None, True),
        ("end", "doctor", '{"json": false}', "ok", 0, 0, False),
    ]
    for row in runs:
        assert row[7:9] == ("0.1.0", "2.0.0")
        assert row[9].startswith("3.12.")
        assert re.fullmatch(r"[0-9a-f]{40}", row[10])

    events = audit_query(
        "SELECT seq, stage, level, error_code, message FROM events WHERE run_id = ? ORDER BY seq",
        [run_id],
    )
    assert events == [
        (1, "check", "info", None, "python: 3.12.13"),
        (2, "check", "info", None, "uv: 0.11.28"),
        (3, "check", "info", None, "node: 24.11.1"),
        (4, "check", "info", None, "quarto: 1.8.25"),
        (5, "check", "info", None, f"data folder: {data_root}"),
        (6, "check", "info", None, "config: display unit crores"),
        (7, "check", "info", None, "audit schema: version 2"),
        (8, "check", "info", None, "dictionary schema: version 1"),
    ]

    records = jsonl_records(data_root, run_id)
    assert [(r["table"], r.get("record"), r.get("seq")) for r in records] == [
        ("runs", "start", None),
        *[("events", None, seq) for seq in range(1, 9)],
        ("runs", "end", None),
    ]
    assert [r["message"] for r in records if r["table"] == "events"] == [e[4] for e in events]
    assert {r["run_id"] for r in records} == {run_id}


def test_first_init_is_recorded_once_the_audit_database_exists(
    data_root: Path, audit_query: Query
) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    run_id = run_id_of(result.stderr)
    assert audit_query(
        "SELECT record, status FROM runs WHERE run_id = ? ORDER BY record DESC", [run_id]
    ) == [("start", "started"), ("end", "ok")]
    assert audit_query(
        "SELECT stage, message FROM events WHERE run_id = ? ORDER BY seq", [run_id]
    ) == [
        ("folders", "11 created, 0 already present"),
        ("config", "wrote default config.toml"),
        ("migrate", "shared/audit.duckdb: schema version 0 -> 2 (backup: none, new file)"),
        ("migrate", "shared/dictionary.duckdb: schema version 0 -> 1 (backup: none, new file)"),
        ("migrate", "shared/analytics.duckdb: schema version 0 -> 1 (backup: none, new file)"),
        (
            "dictionary",
            "dictionary: 75 accounts and 106 label mappings added from templates/dictionary",
        ),
    ]
    assert audit_query(
        "SELECT db_file, from_version, to_version, backup_path, status FROM migrations "
        "WHERE run_id = ?",
        [run_id],
    ) == [
        ("shared/audit.duckdb", 0, 2, None, "applied"),
        ("shared/dictionary.duckdb", 0, 1, None, "applied"),
        ("shared/analytics.duckdb", 0, 1, None, "applied"),
    ]
    assert len(jsonl_records(data_root, run_id)) == 11


def test_runs_are_appended_never_updated(data_root: Path, audit_query: Query) -> None:
    for _ in range(3):
        runner.invoke(app, ["migrate"] if (data_root / "shared").exists() else ["init"])
    assert audit_query("SELECT command, record, count(*) FROM runs GROUP BY ALL ORDER BY ALL") == [
        ("init", "end", 1),
        ("init", "start", 1),
        ("migrate", "end", 2),
        ("migrate", "start", 2),
    ]


def test_forced_error_logs_its_code_and_exits_2(data_root: Path, audit_query: Query) -> None:
    runner.invoke(app, ["init"])
    company = data_root / "companies" / "acme.duckdb"
    with connect(company, write=True) as con:
        con.execute(
            "CREATE TABLE schema_version (version INTEGER, name VARCHAR, "
            "applied_at TIMESTAMP, run_id VARCHAR)"
        )
        con.execute("INSERT INTO schema_version VALUES (99, '099_future', now(), 'x')")

    result = runner.invoke(app, ["migrate"])

    assert result.exit_code == 2
    message = (
        "companies/acme.duckdb is at schema version 99, but this fin knows only up to 2; "
        "update fin (`git pull`, then `uv sync --frozen`)"
    )
    assert result.stderr.splitlines()[0] == f"E004: {message}"
    run_id = run_id_of(result.stderr)
    assert audit_query(
        "SELECT level, error_code, message FROM events WHERE run_id = ?", [run_id]
    ) == [("error", "E004", message)]
    assert audit_query(
        "SELECT status, exit_code FROM runs WHERE run_id = ? AND record = 'end'", [run_id]
    ) == [("error", 2)]
    assert jsonl_records(data_root, run_id)[1]["error_code"] == "E004"


def _guarded_app(body: Callable[[Run], None]) -> typer.Typer:
    test_app = typer.Typer()

    @test_app.command()
    def guarded() -> None:
        execute("guarded", {"target": "x"}, body)

    return test_app


def test_guard_error_logs_e701_and_exits_3(data_root: Path, audit_query: Query) -> None:
    runner.invoke(app, ["init"])

    def body(run: Run) -> None:
        run.event("guarded", "about to be blocked")
        raise GuardBlocked("`fin delete` is human-only")

    result = runner.invoke(_guarded_app(body), [])

    assert result.exit_code == 3
    assert result.stderr.splitlines()[0] == "E701: `fin delete` is human-only"
    run_id = run_id_of(result.stderr)
    assert audit_query(
        "SELECT seq, level, error_code, message FROM events WHERE run_id = ? ORDER BY seq",
        [run_id],
    ) == [
        (1, "info", None, "about to be blocked"),
        (2, "error", "E701", "`fin delete` is human-only"),
    ]
    assert audit_query(
        "SELECT command, args, status, exit_code FROM runs WHERE run_id = ? AND record = 'end'",
        [run_id],
    ) == [("guarded", '{"target": "x"}', "blocked", 3)]


def test_unexpected_exception_is_recorded_and_reraised(data_root: Path, audit_query: Query) -> None:
    runner.invoke(app, ["init"])

    def body(run: Run) -> None:
        raise ValueError("boom")

    result = runner.invoke(_guarded_app(body), [])

    assert isinstance(result.exception, ValueError)
    assert audit_query(
        "SELECT r.status, r.exit_code, e.message FROM runs r JOIN events e USING (run_id) "
        "WHERE r.command = 'guarded' AND r.record = 'end'"
    ) == [("error", 2, "unexpected ValueError: boom")]


def test_command_that_needs_the_audit_database_stops_with_e001_before_init(
    data_root: Path,
) -> None:
    ran: list[str] = []
    result = runner.invoke(_guarded_app(lambda run: ran.append(run.run_id)), [])
    assert result.exit_code == 2
    audit = data_root / "shared" / "audit.duckdb"
    assert result.stderr.splitlines()[0] == (
        f"E001: audit database {audit} does not exist; run `uv run fin init`"
    )
    assert ran == []
