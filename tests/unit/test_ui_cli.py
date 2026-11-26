"""`fin ui`: without the add-on it refuses with E006 and changes nothing; `fin
doctor` reports the UI as installed or not, never as a failure."""

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.doctor import check_web_ui
from fin.ui import setup

runner = CliRunner()


def test_fin_ui_says_how_to_install_when_not_installed(
    data_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setattr(setup, "installed", lambda: False)
    result = runner.invoke(app, ["ui"])
    assert result.exit_code == 2
    assert "E006" in result.output
    assert "fin ui setup" in result.output


def test_doctor_reports_the_ui_as_not_installed_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fin.doctor

    monkeypatch.setattr(fin.doctor, "extra_installed", lambda: False)
    check = check_web_ui()
    assert check.ok and not check.warning
    assert "not installed" in check.detail


def test_doctor_reports_the_ui_as_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import fin.doctor

    monkeypatch.setattr(fin.doctor, "extra_installed", lambda: True)
    check = check_web_ui()
    assert check.ok and not check.warning and check.detail.startswith("installed")
