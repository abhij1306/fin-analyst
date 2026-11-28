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
        installhint.missing("node"),
        installhint.reinstall("node"),
        installhint.upgrade("node"),
        installhint.poppler_reinstall(),
    ]
    assert all(("brew" in hint) == macos for hint in hints)
    assert "./install.sh" in hints[0]


def test_quarto_hints_never_name_brew_or_sudo_on_macos(macos: bool) -> None:
    hints = [
        installhint.missing("quarto"),
        installhint.reinstall("quarto"),
        installhint.upgrade("quarto"),
        installhint.quarto_missing(),
    ]
    assert all("./install.sh" in hint for hint in hints)
    assert not any("brew" in hint for hint in hints)
    if macos:
        assert all("home folder, no sudo" in hint for hint in hints)


def test_doctor_fix_for_old_node_per_os(monkeypatch: pytest.MonkeyPatch, macos: bool) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v20.1.0")
    fix = check_tool("node", (24,)).fix
    assert fix == (
        "run `brew upgrade node`" if macos else "run ./install.sh (installs a current version)"
    )


def test_doctor_fix_for_missing_tool_per_os(monkeypatch: pytest.MonkeyPatch, macos: bool) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: None)
    fix = check_tool("node", (24,)).fix
    assert fix is not None
    assert ("brew bundle" in fix) == macos
    assert ("apt-get" in fix) == (not macos)
    quarto_fix = check_tool("quarto", (1, 7)).fix
    assert quarto_fix is not None
    assert ("apt-get" in quarto_fix) == (not macos)
    assert "brew" not in quarto_fix


def test_install_script_minimums_match_doctor() -> None:
    script = INSTALL_SH.read_text(encoding="utf-8")
    for tool, minimum in TOOL_MINIMUMS.items():
        found = re.search(rf"^MIN_{tool.upper()}=\"([\d.]+)\"", script, re.MULTILINE)
        assert found, f"install.sh has no MIN_{tool.upper()}"
        assert tuple(int(part) for part in found.group(1).split(".")) == minimum


def test_install_script_has_no_quarto_cask_and_pins_the_quarto_ci_renders_with() -> None:
    brewfile = (INSTALL_SH.parent / "Brewfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*cask\s", brewfile, re.MULTILINE)
    ci = (INSTALL_SH.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pinned = re.search(r'^QUARTO_VERSION="([\d.]+)"', INSTALL_SH.read_text(encoding="utf-8"), re.M)
    assert pinned
    assert f"version: {pinned.group(1)}" in ci
