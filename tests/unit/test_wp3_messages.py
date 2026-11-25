"""Messages for a company with no database and a path that does not exist (final audit, WP3)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.audit.errors import FileUnreadable
from fin.cli import app
from fin.ingest import files_in

runner = CliRunner()
MESSAGE = (
    "E001: no database for company ghost; ingest its documents first (`uv run fin ingest ghost`)"
)


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


@pytest.mark.parametrize(
    "args",
    [
        ["sql", "ghost", "--sql", "SELECT 1"],
        ["sql", "ghost", "--write", "--sql", "DELETE FROM facts"],
        ["statements", "ghost"],
        ["report", "ghost"],
        ["review", "list", "ghost"],
    ],
)
def test_an_unknown_company_gives_one_code_and_text(root: Path, args: list[str]) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 2, result.output
    assert MESSAGE in result.stderr


def test_a_missing_path_says_where_a_companys_files_go(tmp_path: Path) -> None:
    with pytest.raises(FileUnreadable, match=r"does not exist; put the company's files in .*inbox"):
        files_in(tmp_path / "inbox" / "ghost")
