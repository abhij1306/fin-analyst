import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app

runner = CliRunner()

EXPECTED_SUBDIRS = {
    "inbox",
    "archive",
    "companies",
    "shared",
    "review",
    "notes",
    "evidence",
    "reports",
    "logs",
    "backups",
}


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == "fin 0.1.0\n"


def test_init_creates_data_folder_tree(data_root: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert {p.name for p in data_root.iterdir()} == EXPECTED_SUBDIRS
    assert result.stdout.endswith(
        f"data folder {data_root.resolve()}: 11 created, 0 already present\n"
    )


def test_init_twice_creates_nothing_and_keeps_files(data_root: Path) -> None:
    runner.invoke(app, ["init"])
    (data_root / "inbox" / "keep.txt").write_text("user file", encoding="utf-8")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert result.stdout == f"data folder {data_root.resolve()}: 0 created, 11 already present\n"
    assert (data_root / "inbox" / "keep.txt").read_text(encoding="utf-8") == "user file"


def test_init_unwritable_location_is_e003(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("FIN_DATA_DIR", str(blocker / "FinAnalysis"))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert result.stderr.startswith("E003: cannot create ")


def _all_tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"uv": "uv 0.11.28", "node": "v24.11.1", "quarto": "1.8.25"}
    monkeypatch.setattr("fin.doctor.tool_version", versions.get)
    monkeypatch.setattr("fin.doctor.sys.version_info", (3, 12, 13, "final", 0))


def test_doctor_passes_after_init(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _all_tools_present(monkeypatch)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok    python: 3.12.13",
        "ok    uv: 0.11.28",
        "ok    node: 24.11.1",
        "ok    quarto: 1.8.25",
        f"ok    data folder: {data_root.resolve()}",
        "all checks passed",
    ]


def test_doctor_json_reports_missing_data_folder(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _all_tools_present(monkeypatch)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["checks"][-1] == {
        "name": "data folder",
        "ok": False,
        "detail": f"{data_root.resolve()} does not exist",
        "code": "E003",
        "fix": "run `uv run fin init`",
    }


def test_doctor_wrong_python_is_e002(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _all_tools_present(monkeypatch)
    monkeypatch.setattr("fin.doctor.sys.version_info", (3, 13, 1, "final", 0))
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[0].startswith(
        "FAIL  python: found 3.13.1, need 3.12.x [E002]"
    )
