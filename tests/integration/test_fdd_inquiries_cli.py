"""Management inquiries end to end through the CLI (docs/fdd.md F2): an inquiry is raised with a
topic and no amount, shows in the checklist, follows the query life cycle, and a mix with
--amount or an unknown topic is refused."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY24,FY25")
    assert opened.exit_code == 0, opened.output
    return data_root


def test_inquiry_raise_checklist_and_life_cycle(root: Path) -> None:
    raised = fin("query", "raise", "acme", "--topic", "tax", "--question", "Any open notices?")
    assert raised.exit_code == 0, raised.output
    assert "inquiry: tax" in raised.output
    sheet = fin("query", "checklist", "acme")
    assert sheet.exit_code == 0, sheet.output
    assert "tax: 1 inquiries" in sheet.output
    assert "revenue_recognition: none yet" in sheet.output and "standard:" in sheet.output
    assert fin("query", "approve", "acme", "1").exit_code == 0
    assert fin("query", "sent", "acme", "1").exit_code == 0
    answered = fin("query", "answer", "acme", "1", "--text", "None open")
    assert answered.exit_code == 0, answered.output
    assert "answer: None open" in fin("query", "checklist", "acme").output


def test_bad_topic_and_mixed_options_are_refused(root: Path) -> None:
    bad = fin("query", "raise", "acme", "--topic", "weather", "--question", "x")
    assert bad.exit_code != 0 and "E910" in bad.output
    mixed = fin("query", "raise", "acme", "--topic", "tax", "--question", "x", "--amount", "100")
    assert mixed.exit_code != 0
    assert "no queries" in fin("query", "list", "acme").output
