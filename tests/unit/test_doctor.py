from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.doctor import Check, check_audit_schema, check_tool, parse_version
from fin.paths import audit_db_path


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("uv 0.11.28 (ebf0f43d7 2026-07-07 x86_64-apple-darwin)", (0, 11, 28)),
        ("v24.11.1", (24, 11, 1)),
        ("1.8.25", (1, 8, 25)),
        ("no digits here", None),
    ],
)
def test_parse_version(output: str, expected: tuple[int, ...] | None) -> None:
    assert parse_version(output) == expected


def test_tool_missing_is_e001(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: None)
    check = check_tool("quarto", (1, 7))
    assert (check.ok, check.code, check.detail) == (False, "E001", "not found on PATH")


def test_tool_below_minimum_is_e002(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v22.3.0")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (False, "E002", "found 22.3.0, need >= 24")
    assert check.fix == "run `brew upgrade node`"


def test_tool_at_or_above_minimum_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v26.8.1")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (True, None, "26.8.1")


def test_audit_schema_check_names_the_fix(data_root: Path, migrations_dir: Path) -> None:
    CliRunner().invoke(app, ["init"])
    assert check_audit_schema(data_root) == Check("audit schema", True, "version 3")

    (migrations_dir / "audit" / "004_next.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert check_audit_schema(data_root) == Check(
        "audit schema", False, "version 3, expected 4", "E002", "run `uv run fin migrate`"
    )

    (migrations_dir / "audit" / "004_next.sql").unlink()
    with connect(audit_db_path(data_root), write=True) as con:
        con.execute("INSERT INTO schema_version VALUES (4, '004_future', now(), 'x')")
    assert check_audit_schema(data_root) == Check(
        "audit schema",
        False,
        "version 4 is newer than this fin (3)",
        "E004",
        "update fin: `git pull`, then `uv sync --frozen`",
    )
