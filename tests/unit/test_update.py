import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import fin.update
from fin.cli import app

runner = CliRunner()


def test_update_that_cannot_start_a_program_is_e005(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr("fin.update.shutil.which", lambda name: "git")
    monkeypatch.setattr("fin.update.subprocess.run", refuse)

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 2
    assert "E005: git status: cannot start git: not allowed" in result.output


def test_failed_doctor_after_update_shows_its_check_and_both_run_ids(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    doctor_out = (
        "ok    python: 3.12.1\nFAIL  node: not found on PATH [E001] fix: run ./install.sh\n"
    )
    doctor_err = (
        "summary: loaded 0, skipped 0, failed 0, pending review 0, held 0, run_id DOC-123\n"
    )

    def fake_run(
        run: Any, step: str, command: list[str], cwd: Path, *, must_succeed: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if step == "fin doctor":
            return subprocess.CompletedProcess(command, 1, doctor_out, doctor_err)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(fin.update, "_run", fake_run)

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 1
    assert "FAIL  node: not found on PATH [E001] fix: run ./install.sh" in result.output
    assert "fin doctor run_id DOC-123" in result.output
    assert "ok    python" not in result.output
    assert "update finished; fin doctor failed (update run_id 2" in result.output
