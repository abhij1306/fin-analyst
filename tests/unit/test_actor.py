"""The actor (fin.actor, SPEC-02): passed in by the interface, stored as 'user' or
'ai:<provider>'."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin import actor
from fin.cli import app
from tests.engagement_root import mapping_decisions, with_proposal


def test_the_cli_actor_is_claude_only_inside_claude_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert actor.from_environment() == "ai:claude"
    monkeypatch.delenv("CLAUDECODE")
    assert actor.from_environment() == "user"


def test_a_proposal_is_the_ais_and_an_ai_decision_is_on_request() -> None:
    assert actor.recorded("user", "proposed") == ("ai:claude", False)
    assert actor.recorded("user", "confirmed") == ("user", False)
    assert actor.recorded("ai:claude", "rejected") == ("ai:claude", True)
    assert actor.recorded("ai:other", "edited") == ("ai:other", True)


def test_readers_name_an_actor_by_its_prefix() -> None:
    assert [actor.is_ai(a) for a in ("ai:claude", "ai:other", "user", "fin")] == [
        True,
        True,
        False,
        False,
    ]
    assert actor.label("ai:claude") == "Claude"
    assert actor.label("ai:other") == "other"
    assert actor.label("user") == "the user"
    assert actor.label("docling") == "docling"


def test_the_cli_inside_claude_code_records_claude_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_proposal(tmp_path)
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    monkeypatch.setenv("CLAUDECODE", "1")
    result = CliRunner().invoke(app, ["review", "reject", "acme", "1"])
    assert result.exit_code == 0, result.output
    assert mapping_decisions(root) == [("rejected", "ai:claude", True)]
