import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.config import set_parser
from fin.db.conn import connect
from fin.doctor import (
    REPO,
    TEMP_ROOTS,
    Check,
    check_audit_schema,
    check_pdf_parser,
    check_python_source,
    check_repo_folder,
    check_tool,
    in_temp_folder,
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
    monkeypatch.setattr("fin.systools.which", lambda tool: tool)

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


def test_python_source_is_ok_on_uvs_own_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.uv_python_dir", lambda: Path(sys.base_prefix).resolve().parent)
    assert check_python_source() == Check("python source", True, "uv's own Python")


def test_python_source_warns_on_a_python_from_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("fin.doctor.uv_python_dir", lambda: tmp_path / "uv-python")
    check = check_python_source()
    assert (check.ok, check.warning) == (True, True)
    assert check.detail.endswith("is not uv's own Python (one found on PATH was used)")
    assert check.fix == "run ./install.sh (it makes .venv again with uv's own Python)"


def test_python_source_warns_when_uv_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.uv_python_dir", lambda: None)
    check = check_python_source()
    assert (check.ok, check.warning) == (True, True)
    assert "did not answer" in check.detail


def test_repo_in_a_temporary_folder_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    repo = tmp_path / "workspace" / "fin-analyst"
    assert in_temp_folder(repo)
    assert in_temp_folder(Path("/var/folders/x1/T/fin-analyst"))
    monkeypatch.setattr("fin.doctor.REPO", repo)
    check = check_repo_folder()
    assert (check.ok, check.warning) == (True, True)
    assert check.detail == f"{repo} is in a temporary folder, which the system or an app can delete"
    assert check.fix is not None and "~/fin-analyst" in check.fix


def test_repo_in_a_chosen_folder_is_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr("fin.doctor.tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    chosen = tmp_path / "home" / "fin-analyst"
    assert not in_temp_folder(chosen)
    monkeypatch.setattr("fin.doctor.REPO", chosen)
    assert check_repo_folder() == Check("repo folder", True, str(chosen))


def test_install_script_temp_roots_match_doctor() -> None:
    script = (REPO / "install.sh").read_text(encoding="utf-8")
    found = re.search(r"^TEMP_ROOTS=\((.*)\)$", script, re.MULTILINE)
    assert found, "install.sh has no TEMP_ROOTS"
    assert tuple(found.group(1).split()) == TEMP_ROOTS
