"""`fin query list` and `fin adjust list` with no open engagement say so and exit 0; commands that
change something keep E803 (client run, 2026-11-27)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import audit_db_path, company_db_path


@pytest.fixture
def root(data_root: Path) -> Path:
    (data_root / "logs").mkdir(parents=True)
    ensure_database(audit_db_path(data_root), "audit")
    ensure_database(company_db_path(data_root, "acme"), "company")
    return data_root


@pytest.mark.parametrize(("kind", "noun"), [("query", "queries"), ("adjust", "adjustments")])
def test_a_list_with_no_open_engagement_says_so_and_exits_0(
    root: Path, kind: str, noun: str
) -> None:
    runner = CliRunner()

    listed = runner.invoke(app, [kind, "list", "acme"])
    as_json = runner.invoke(app, [kind, "list", "acme", "--json"])

    assert (listed.exit_code, listed.stdout) == (0, f"acme has no open engagement: no {noun}\n")
    assert (as_json.exit_code, as_json.stdout) == (0, "[]\n")


def test_a_command_that_changes_something_keeps_e803(root: Path) -> None:
    result = CliRunner().invoke(app, ["adjust", "accept", "acme", "1"])

    assert result.exit_code == 2
    assert result.stderr.startswith("E803: acme has no open engagement; nothing changed.")
