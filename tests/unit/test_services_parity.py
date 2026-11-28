"""SPEC-02 PR 1: each decision the UI offers calls the same service as its CLI command, so pressing
it in the UI and running the command give the same run record apart from `via`."""

import json
import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.actor import USER
from fin.audit.logger import Run
from fin.cli import app
from fin.db.conn import connect
from fin.diligence import queries
from fin.map.dictionary import mapping_row
from fin.paths import audit_db_path, dictionary_db_path
from fin.services import decisions
from fin.services.reviews import KINDS
from fin.ui import actions
from tests.engagement_root import engagement_root

runner = CliRunner()


def _raise(root: Path) -> None:
    queries.raise_manual(
        Run("query raise", {}, root),
        "acme",
        amount=Decimal(500000),
        period="FY25",
        scope=None,
        sources="a.xlsx Sheet1!B2",
        question="Why?",
    )


def _raise_and_approve(root: Path) -> None:
    _raise(root)
    decisions.set_query_status(root, "acme", [1], "approved", via="cli", actor=USER)


def _nothing(root: Path) -> None:
    pass


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return engagement_root(tmp_path_factory.mktemp("template"))


def _roots(template: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Two copies of one data folder: building it takes seconds, copying it milliseconds."""
    return (
        Path(shutil.copytree(template, tmp_path / "ui")),
        Path(shutil.copytree(template, tmp_path / "cli")),
    )


# action, item, reason, the CLI's arguments, setup run in both data folders
CASES: list[tuple[str, str, str, list[str], Callable[[Path], None]]] = [
    ("mapping-confirm", "99", "", ["review", "confirm", "acme", "99"], _nothing),
    ("mapping-reject", "99", "", ["review", "reject", "acme", "99"], _nothing),
    ("finding-accept", "99", "", ["findings", "accept", "acme", "99"], _nothing),
    ("document-delete", "99", "", ["document", "delete", "acme", "99"], _nothing),
    ("query-approve", "1", "", ["query", "approve", "acme", "1"], _raise),
    ("query-sent", "1", "", ["query", "sent", "acme", "1"], _raise_and_approve),
    ("query-close", "1", "", ["query", "close", "acme", "1"], _raise),
    (
        "query-withdraw",
        "1",
        "duplicate",
        ["query", "withdraw", "acme", "1", "--reason", "duplicate"],
        _raise,
    ),
    ("adjust-accept", "99", "", ["adjust", "accept", "acme", "99"], _nothing),
    (
        "adjust-reject",
        "99",
        "no evidence",
        ["adjust", "reject", "acme", "99", "--reason", "no evidence"],
        _nothing,
    ),
    ("gate-approve", "g2", "", ["gate", "approve", "acme", "G2"], _nothing),
    ("fdd-final", "99", "", ["fdd", "finding", "final", "acme", "99"], _nothing),
    ("fdd-draft", "99", "", ["fdd", "finding", "final", "acme", "99", "--undo"], _nothing),
    ("mapping-confirm-all", "99,98", "", ["review", "confirm", "acme", "99", "98"], _nothing),
    ("reference-confirm", "99", "", ["reference", "confirm", "acme", "99"], _nothing),
    ("reference-reject", "99", "", ["reference", "reject", "acme", "99"], _nothing),
    ("dataroom-approve", "99", "", ["dataroom", "approve", "acme", "99"], _nothing),
    ("dataroom-undo", "99", "", ["dataroom", "approve", "acme", "99", "--undo"], _nothing),
] + [
    (f"review-{verb}-{kind}", "99", "", ["review", verb, "acme", "99", f"--{kind}"], _nothing)
    for kind in KINDS
    for verb in ("confirm", "reject")
]
# Its own test below: it needs a mapping made first.
SEPARATE = ["mapping-change"]


def _last_run(root: Path) -> dict[str, Any]:
    with connect(audit_db_path(root)) as con:
        run_id, command, args, status, exit_code = con.execute(
            "SELECT run_id, command, args, status, exit_code FROM runs WHERE record = 'end' "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        events = con.execute(
            "SELECT stage, file, level, error_code, message FROM events WHERE run_id = ? "
            "ORDER BY seq",
            [run_id],
        ).fetchall()
    return {
        "command": command,
        "args": json.loads(args),
        "status": status,
        "exit_code": exit_code,
        "events": events,
    }


def test_every_ui_action_has_a_parity_case() -> None:
    assert sorted([case[0] for case in CASES] + SEPARATE) == sorted(actions.ACTIONS)


@pytest.mark.parametrize(("action", "item", "reason", "argv", "setup"), CASES)
def test_the_ui_and_the_cli_leave_the_same_run_record(
    template: Path,
    tmp_path: Path,
    action: str,
    item: str,
    reason: str,
    argv: list[str],
    setup: Callable[[Path], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)  # the CLI's actor is the user's here too
    ui_root, cli_root = _roots(template, tmp_path)
    setup(ui_root)
    setup(cli_root)

    pressed = actions.decide(ui_root, "acme", action, item, reason)
    ran = runner.invoke(app, argv, env={"FIN_DATA_DIR": str(cli_root)})

    ui_run, cli_run = _last_run(ui_root), _last_run(cli_root)
    assert ui_run["args"].pop("via") == "ui"
    assert cli_run["args"].pop("via") == "cli"
    assert ui_run == cli_run
    assert ui_run["command"] == " ".join(argv[:3] if argv[0] == "fdd" else argv[:2])
    assert pressed.ok == (ran.exit_code == 0)
    if not pressed.ok:
        assert pressed.code is not None
        assert ran.stderr.startswith(f"{pressed.code}: ")


def test_a_successful_press_shows_what_the_cli_prints(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    ui_root, cli_root = _roots(template, tmp_path)
    _raise(ui_root)
    _raise(cli_root)
    pressed = actions.decide(ui_root, "acme", "query-approve", "1", "")
    ran = runner.invoke(app, ["query", "approve", "acme", "1"], env={"FIN_DATA_DIR": str(cli_root)})
    assert pressed.ok, pressed.lines
    assert ran.exit_code == 0, ran.output
    assert ran.stdout == "".join(f"{line}\n" for line in pressed.lines)
    assert "approved" in pressed.lines[0]


def _map_sundry(root: Path) -> int:
    key = ("profit_and_loss", "", "Sundry receipts")
    assert decisions.set_mapping(root, "acme", key, "finance_cost", via="cli", actor=USER).ok
    with connect(dictionary_db_path(root)) as con:
        [(mapping_id,)] = con.execute(
            "SELECT mapping_id FROM label_map WHERE source = 'map set'"
        ).fetchall()
    return int(mapping_id)


def test_changing_the_mapping_behind_a_finding_is_fin_map_set(
    template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    ui_root, cli_root = _roots(template, tmp_path)
    mapping_id = _map_sundry(ui_root)
    statement, section, label = mapping_row(cli_root, _map_sundry(cli_root))
    argv = ["map", "set", "acme", "--statement", statement, "--label", label]
    argv += ["--account", "other_income", "--section", section]

    pressed = actions.decide(ui_root, "acme", "mapping-change", str(mapping_id), "", "other_income")
    ran = runner.invoke(app, argv, env={"FIN_DATA_DIR": str(cli_root)})

    assert pressed.ok, pressed.lines
    assert ran.exit_code == 0, ran.output
    ui_run, cli_run = _last_run(ui_root), _last_run(cli_root)
    assert ui_run["args"].pop("via") == "ui"
    assert cli_run["args"].pop("via") == "cli"
    assert ui_run == cli_run
