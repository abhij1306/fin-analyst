"""Ingest and delete safety nets (final audit, WP2): an out-of-range figure fails its file with a
coded reason and the next file still loads; a failed first ingest leaves no company database; two
different PDFs with one name keep their own notes; `fin delete` leaves nothing of the company."""

import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config
from fin.db.conn import connect
from fin.delete import delete_company
from fin.ingest import read_document, sha256_of
from fin.paths import company_db_path

runner = CliRunner()
SAMPLES = Path(__file__).parent.parent / "fixtures" / "sample-financials" / "clean"
KESTREL = SAMPLES / "kestrel" / "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
TARANG = SAMPLES / "tarang"


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def one(root: Path, company: str, sql: str) -> list[tuple[object, ...]]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def test_an_out_of_range_figure_fails_its_file_and_the_next_one_loads(
    root: Path, tmp_path: Path
) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    book = load_workbook(KESTREL, data_only=True)  # cached values: a saved formula has none
    sheet = book.worksheets[0]
    cell = max(
        (c for r in sheet for c in r if isinstance(c.value, int | float)), key=lambda c: c.value
    )
    cell.value = 10**14  # in lakh: 10^19 rupees, more than facts.value can hold
    book.save(inbox / "a_bad.xlsx")
    shutil.copy(KESTREL, inbox / "b_good.xlsx")

    result = runner.invoke(app, ["ingest", "kestrel", str(inbox)])

    assert result.exit_code == 1, result.output
    reason = json.loads((root / "review/kestrel/a_bad.xlsx.error.json").read_text("utf-8"))
    assert reason["code"] == "E890"
    assert f"{sheet.title}!{cell.coordinate}" in reason["message"]
    assert one(root, "kestrel", "SELECT file_name FROM documents") == [("b_good.xlsx",)]


def test_a_first_ingest_that_loads_nothing_leaves_no_company_database(
    root: Path, tmp_path: Path
) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("not a financial statement")

    result = runner.invoke(app, ["ingest", "newco", str(junk)])

    assert result.exit_code == 1, result.output
    assert not company_db_path(root, "newco").exists()
    assert (root / "review/newco/notes.txt.error.json").is_file()


def test_a_failed_ingest_never_removes_an_existing_company(root: Path, tmp_path: Path) -> None:
    assert runner.invoke(app, ["ingest", "kestrel", str(KESTREL)]).exit_code == 0
    junk = tmp_path / "notes.txt"
    junk.write_text("not a financial statement")

    assert runner.invoke(app, ["ingest", "kestrel", str(junk)]).exit_code == 1

    assert one(root, "kestrel", "SELECT count(*) FROM documents") == [(1,)]


def test_two_different_pdfs_with_one_name_keep_their_own_notes(root: Path, tmp_path: Path) -> None:
    pdfs = sorted(TARANG.glob("*.pdf"))[:2]
    for folder, pdf in zip(("a", "b"), pdfs, strict=True):
        (tmp_path / folder).mkdir()
        shutil.copy(pdf, tmp_path / folder / "report.pdf")
    config, run = load_config(root), Run("ingest", {}, root)

    first = read_document(run, tmp_path / "a" / "report.pdf", "tarang", config).notes
    second = read_document(run, tmp_path / "b" / "report.pdf", "tarang", config).notes

    assert first[0][1] != second[0][1]
    assert sha256_of(tmp_path / "a" / "report.pdf")[:12] in first[0][1].parts
    texts = {p.read_text("utf-8") for _, p in first + second}
    assert len(texts) == len(first) + len(second)  # no file was overwritten by the other PDF


def test_delete_removes_exports_evals_and_cached_parses_and_backs_them_up(
    root: Path,
) -> None:
    assert runner.invoke(app, ["ingest", "kestrel", str(KESTREL)]).exit_code == 0
    sha = sha256_of(KESTREL)
    kept = [
        root / "exports/kestrel/r1/queries.txt",
        root / "evals/kestrel/golden.csv",
        root / f"cache/bank/{sha}.json",
        root / f"cache/llamaparse/{sha}-p1.json",
    ]
    other = root / "cache/bank/ffff.json"
    for path in [*kept, other]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    payload = delete_company(Run("delete", {}, root), "kestrel")

    assert not any(path.exists() for path in kept)
    assert other.exists()  # another file's parse stays
    backup = root / payload["backup_path"]
    assert all((backup / path.relative_to(root)).is_file() for path in kept)
    assert "label mappings stay in shared/dictionary.duckdb" in str(payload["note"])
