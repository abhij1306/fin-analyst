import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.config import set_parser
from fin.db.conn import connect
from fin.doctor import (
    Check,
    check_audit_schema,
    check_pdf_parser,
    check_tool,
    parse_version,
    run_checks,
)
from fin.paths import audit_db_path


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("uv 0.11.28 (ebf0f43d7 2026-07-07 x86_64-apple-darwin)", (0, 11, 28)),
        ("v24.11.1", (24, 11, 1)),
        ("1.8.25", (1, 8, 25)),
        ("Quarto 1.7", (1, 7)),
        ("build 20260707 then 2.3", (2, 3)),
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
    monkeypatch.setattr("fin.installhint.is_macos", lambda: True)
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v22.3.0")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (False, "E002", "found 22.3.0, need >= 24")
    assert check.fix == "run `brew upgrade node`"


def test_tool_at_or_above_minimum_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v26.8.1")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (True, None, "26.8.1")


def test_tool_version_timeout_fails_that_check_and_the_rest_still_run(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(app, ["init"])
    monkeypatch.setattr("fin.doctor.shutil.which", lambda tool: tool)

    def hang(command: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr("fin.doctor.subprocess.run", hang)

    checks = {check.name: check for check in run_checks(data_root)}

    assert (checks["node"].ok, checks["node"].code) == (False, "E001")
    assert "did not run" in checks["node"].detail
    assert (checks["poppler"].ok, checks["poppler"].warning) == (True, True)
    assert checks["config"].ok is True


def test_audit_schema_check_names_the_fix(data_root: Path) -> None:
    CliRunner().invoke(app, ["init"])
    assert check_audit_schema(data_root) == Check("audit schema", True, "present")

    with connect(audit_db_path(data_root), write=True) as con:
        con.execute("DROP TABLE deletions")  # a table this fin's schema creates
    assert check_audit_schema(data_root) == Check(
        "audit schema",
        False,
        "made by an older fin",
        "E002",
        "move the data folder away and re-create it with `uv run fin init`",
    )


def test_no_pdf_add_on_is_fine(data_root: Path) -> None:
    CliRunner().invoke(app, ["init"])
    assert check_pdf_parser(data_root) == Check(
        "pdf parser", True, "none (optional; `uv run fin parser use <name>` or /parser)"
    )


def test_a_chosen_add_on_that_does_not_import_fails(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(app, ["init"])
    set_parser(data_root, "liteparse")

    def missing(name: str) -> None:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr("fin.doctor.importlib.import_module", missing)
    check = check_pdf_parser(data_root)
    assert (check.ok, check.code, check.fix) == (
        False,
        "E001",
        "run `uv run fin parser use liteparse` (or `uv run fin parser use none`)",
    )
    assert check.detail.startswith('[pdf] parser = "liteparse" in config.toml, but LiteParse')


def test_docling_without_its_models_warns(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(app, ["init"])
    set_parser(data_root, "docling")
    monkeypatch.setattr("fin.doctor.importlib.import_module", lambda name: None)
    monkeypatch.setenv("DOCLING_CACHE_DIR", str(tmp_path / "empty"))
    check = check_pdf_parser(data_root)
    assert (check.ok, check.warning, check.fix) == (
        True,
        True,
        "run `uv run docling-tools models download layout tableformer rapidocr`",
    )


def test_llamaparse_says_whether_the_key_is_set_never_its_value(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CliRunner().invoke(app, ["init"])
    set_parser(data_root, "llamaparse")
    monkeypatch.setattr("fin.doctor.importlib.import_module", lambda name: None)
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)

    missing = check_pdf_parser(data_root)
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "llx-secret-value")
    present = check_pdf_parser(data_root)

    assert (missing.ok, missing.warning) == (True, True)
    assert "LLAMA_CLOUD_API_KEY is not set" in missing.detail
    assert (present.ok, present.warning) == (True, False)
    assert present.detail == (
        "llamaparse: installed, LLAMA_CLOUD_API_KEY is set; skipped pages are uploaded to "
        "LlamaParse's cloud"
    )
    assert "llx-secret-value" not in str(present.as_dict())
