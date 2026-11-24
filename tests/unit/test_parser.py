"""`fin parser show|use` (fin.parser): the slow steps (uv sync, the model download) are swapped for
harmless commands; what is run, logged and written to config.toml is checked."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import fin.parser
from fin.cli import app
from fin.config import load_config, set_parser

runner = CliRunner()


@pytest.fixture
def steps(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Records each command `fin parser use` runs (they all succeed)."""
    assert runner.invoke(app, ["init"]).exit_code == 0
    ran: list[list[str]] = []
    real = fin.parser.subprocess.run

    def run(command: list[str], **kwargs: Any) -> Any:
        ran.append(command[1:])
        return real([sys.executable, "-c", "pass"], **kwargs)

    monkeypatch.setattr(fin.parser.subprocess, "run", run)
    monkeypatch.setattr(fin.parser, "UV_SYNC", [sys.executable, "sync", "--frozen"])
    return ran


def test_use_syncs_the_extra_downloads_models_and_records_it(
    steps: list[list[str]], data_root: Path, audit_query: Callable[..., list[Any]]
) -> None:
    result = runner.invoke(app, ["parser", "use", "liteparse"])

    assert result.exit_code == 0, result.output
    assert steps[0] == ["sync", "--frozen", "--extra", "liteparse"]
    assert steps[1][:2] == ["-I", "-c"] and "ENGINES['liteparse']" in steps[1][2]
    assert load_config(data_root).pdf_parser == "liteparse"
    assert result.stdout.splitlines() == [
        "done     uv sync --extra liteparse",
        "done     LiteParse models",
        'set      [pdf] parser = "liteparse" in config.toml (was "none")',
        "LiteParse: local; small and fast",
    ]
    events = [m for (m,) in audit_query("SELECT message FROM events WHERE stage = 'parser'")]
    assert 'set [pdf] parser = "liteparse" in config.toml (was "none")' in events


def test_none_removes_every_add_on(steps: list[list[str]], data_root: Path) -> None:
    runner.invoke(app, ["parser", "use", "docling"])

    result = runner.invoke(app, ["parser", "use", "none"])

    assert result.exit_code == 0, result.output
    assert steps[-1] == ["sync", "--frozen"]
    assert load_config(data_root).pdf_parser == "none"


def test_llamaparse_says_pages_are_uploaded_and_whether_the_key_is_set(
    steps: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "llx-secret-value")

    result = runner.invoke(app, ["parser", "use", "llamaparse"])

    assert result.exit_code == 0, result.output
    assert steps == [["sync", "--frozen", "--extra", "llamaparse"]]  # no models to download
    assert "uploads the pages pdfplumber skips (only those) to LlamaParse's cloud" in result.output
    assert "LLAMA_CLOUD_API_KEY is set" in result.output
    assert "llx-secret-value" not in result.output


def test_an_engine_this_mac_cannot_run_is_refused_before_anything_changes(
    steps: list[list[str]], data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fin.extract.docling_run.unsupported", lambda: "no Intel Macs")

    result = runner.invoke(app, ["parser", "use", "docling"])

    assert result.exit_code != 0
    assert "E001: no Intel Macs" in result.output
    assert steps == [] and load_config(data_root).pdf_parser == "none"


def test_a_failing_sync_is_e005_and_config_is_unchanged(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setattr(fin.parser, "UV_SYNC", [sys.executable, "-c", "raise SystemExit(3)"])

    result = runner.invoke(app, ["parser", "use", "liteparse"])

    assert result.exit_code != 0
    assert "E005: uv sync --extra liteparse failed (exit 3)" in result.output
    assert load_config(data_root).pdf_parser == "none"


def test_an_unknown_name_is_refused(data_root: Path) -> None:
    result = runner.invoke(app, ["parser", "use", "tesseract"])
    assert result.exit_code == 2


def test_show_names_the_choice_and_whether_it_is_installed(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setattr("fin.extract.liteparse_run.installed", lambda: False)
    shown = runner.invoke(app, ["parser", "show"]).stdout
    assert shown.splitlines() == [
        '[pdf] parser = "none" (no PDF add-on: pages pdfplumber skips are listed as not read)',
        "choices: none, docling, liteparse, llamaparse",
    ]

    set_parser(data_root, "liteparse")
    lines = runner.invoke(app, ["parser", "show"]).stdout.splitlines()

    assert lines[1] == "  not installed: run `uv run fin parser use liteparse`"
