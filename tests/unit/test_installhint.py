import re
from pathlib import Path

import pytest

from fin import installhint
from fin.doctor import TOOL_MINIMUMS, check_tool

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"


@pytest.fixture(params=[True, False], ids=["macos", "linux"])
def macos(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr("fin.installhint.is_macos", lambda: request.param)
    return bool(request.param)


def test_hints_name_the_os_package_manager(macos: bool) -> None:
    hints = [
        installhint.missing(),
        installhint.reinstall("node"),
        installhint.upgrade("node"),
        installhint.quarto_missing(),
        installhint.poppler_reinstall(),
    ]
    assert all(("brew" in hint) == macos for hint in hints)
    assert "./install.sh" in hints[0]
    assert ("./install.sh" in hints[3]) == (not macos)


def test_doctor_fix_for_old_node_per_os(monkeypatch: pytest.MonkeyPatch, macos: bool) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v20.1.0")
    fix = check_tool("node", (24,)).fix
    assert fix == (
        "run `brew upgrade node`" if macos else "run ./install.sh (installs a current version)"
    )


def test_doctor_fix_for_missing_tool_per_os(monkeypatch: pytest.MonkeyPatch, macos: bool) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: None)
    fix = check_tool("quarto", (1, 7)).fix
    assert fix is not None
    assert ("brew bundle" in fix) == macos
    assert ("apt-get" in fix) == (not macos)


def test_install_script_minimums_match_doctor() -> None:
    script = INSTALL_SH.read_text(encoding="utf-8")
    for tool, minimum in TOOL_MINIMUMS.items():
        found = re.search(rf"^MIN_{tool.upper()}=\"([\d.]+)\"", script, re.MULTILINE)
        assert found, f"install.sh has no MIN_{tool.upper()}"
        assert tuple(int(part) for part in found.group(1).split(".")) == minimum
