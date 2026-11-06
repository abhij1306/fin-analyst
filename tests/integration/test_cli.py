import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.config import DEFAULT_CONFIG_TOML

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
    assert result.stdout == "fin 1.0.0\n"


def test_init_creates_data_folder_config_and_shared_databases(data_root: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert {p.name for p in data_root.iterdir()} == EXPECTED_SUBDIRS | {"config.toml"}
    assert (data_root / "config.toml").read_text(encoding="utf-8") == DEFAULT_CONFIG_TOML
    assert sorted(p.name for p in (data_root / "shared").iterdir()) == [
        "analytics.duckdb",
        "audit.duckdb",
        "dictionary.duckdb",
    ]
    assert result.stdout.splitlines()[-6:] == [
        "created  config.toml",
        "shared/audit.duckdb: schema version 0 -> 2 (backup: none, new file)",
        "shared/dictionary.duckdb: schema version 0 -> 1 (backup: none, new file)",
        "shared/analytics.duckdb: schema version 0 -> 1 (backup: none, new file)",
        "dictionary: 75 accounts and 175 label mappings added from templates/dictionary",
        f"data folder {data_root}: 11 created, 0 already present",
    ]


def test_init_twice_creates_nothing_and_overwrites_nothing(data_root: Path) -> None:
    runner.invoke(app, ["init"])
    (data_root / "inbox" / "keep.txt").write_text("user file", encoding="utf-8")
    edited = DEFAULT_CONFIG_TOML.replace('unit = "crores"', 'unit = "lakhs"')
    (data_root / "config.toml").write_text(edited, encoding="utf-8")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "kept     config.toml (already present, not changed)",
        "shared/audit.duckdb: up to date at schema version 2",
        "shared/dictionary.duckdb: up to date at schema version 1",
        "shared/analytics.duckdb: up to date at schema version 1",
        "dictionary: 0 accounts and 0 label mappings added from templates/dictionary",
        f"data folder {data_root}: 0 created, 11 already present",
    ]
    assert (data_root / "inbox" / "keep.txt").read_text(encoding="utf-8") == "user file"
    assert (data_root / "config.toml").read_text(encoding="utf-8") == edited
    assert list((data_root / "backups").iterdir()) == []


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
        f"ok    data folder: {data_root}",
        "ok    config: display unit crores",
        "ok    audit schema: version 2",
        "ok    dictionary schema: version 1",
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
    assert payload["checks"][-4:] == [
        {
            "name": "data folder",
            "ok": False,
            "detail": f"{data_root} does not exist",
            "code": "E003",
            "fix": "run `uv run fin init`",
        },
        {
            "name": "config",
            "ok": False,
            "detail": f"{data_root / 'config.toml'} is missing; run `uv run fin init` to create it",
            "code": "E001",
            "fix": "run `uv run fin init`",
        },
        {
            "name": "audit schema",
            "ok": False,
            "detail": f"{data_root / 'shared' / 'audit.duckdb'} does not exist",
            "code": "E001",
            "fix": "run `uv run fin init`",
        },
        {
            "name": "dictionary schema",
            "ok": False,
            "detail": f"{data_root / 'shared' / 'dictionary.duckdb'} does not exist",
            "code": "E001",
            "fix": "run `uv run fin init`",
        },
    ]
    assert not data_root.exists()


def test_doctor_reports_invalid_config_as_e002(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _all_tools_present(monkeypatch)
    runner.invoke(app, ["init"])
    config = data_root / "config.toml"
    config.write_text(
        DEFAULT_CONFIG_TOML.replace('unit = "crores"', 'unit = "millions"'), encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert (
        f"FAIL  config: {config} is invalid: display.unit = 'millions'; expected one of rupees, "
        "thousands, lakhs, crores [E002]"
    ) in result.stdout


def test_doctor_wrong_python_is_e002(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _all_tools_present(monkeypatch)
    monkeypatch.setattr("fin.doctor.sys.version_info", (3, 13, 1, "final", 0))
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[0].startswith(
        "FAIL  python: found 3.13.1, need 3.12.x [E002]"
    )
