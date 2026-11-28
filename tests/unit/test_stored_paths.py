"""A path read from a database row is checked against the data folder before it is copied,
removed or read (docs/engineering.md rule 8): document delete and re-ingest refuse one that leaves
it (E013) and change nothing."""

from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path
from fin.services.ingest import reingest_document

runner = CliRunner()


@pytest.fixture(params=["archive_path", "reading_path"])
def outside(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Path:
    """A loaded document whose archive_path, or reading_path, was changed to a file outside the
    data folder."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    assert runner.invoke(app, ["init"]).exit_code == 0
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Balance Sheet"
    for row in (
        ["Balance Sheet"],
        ["(Rs. in Lakhs)"],
        ["Particulars", "Note", "31.03.2025", "31.03.2024"],
        ["Borrowings", "5", 1200.5, 1000],
    ):
        sheet.append(row)
    book.save(tmp_path / "bs.xlsx")
    result = runner.invoke(app, ["ingest", "acme", str(tmp_path / "bs.xlsx")])
    assert result.exit_code == 0, result.output
    target = tmp_path / "keep.txt"
    target.write_text("not fin's", encoding="utf-8")
    with connect(company_db_path(data_root, "acme"), write=True) as con:
        con.execute(f"UPDATE documents SET {request.param} = '../keep.txt'")  # a column name
    return target


def test_document_delete_refuses_a_stored_path_outside_the_data_folder(
    data_root: Path, outside: Path
) -> None:
    result = runner.invoke(app, ["document", "delete", "acme", "1"])

    assert result.exit_code == 2
    assert "E013" in result.output
    assert outside.read_text(encoding="utf-8") == "not fin's"
    with connect(company_db_path(data_root, "acme")) as con:
        assert con.execute("SELECT count(*) FROM documents").fetchone() == (1,)


def test_reingest_refuses_a_stored_path_outside_the_data_folder(
    data_root: Path, outside: Path
) -> None:
    result = reingest_document(data_root, "acme", 1, via="cli", actor="user")

    assert (result.ok, result.code) == (False, "E013")
    with connect(company_db_path(data_root, "acme")) as con:
        assert con.execute("SELECT count(*) FROM documents").fetchone() == (1,)
