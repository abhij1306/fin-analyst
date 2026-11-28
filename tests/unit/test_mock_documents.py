"""The committed mock set (mock/) holds what its generator makes: cell values for workbooks, text
for the answer key, bytes for the PDFs (fin's own writer) and the zip's members."""

import zipfile
from datetime import datetime
from pathlib import Path

import pytest
import xlrd
from openpyxl import load_workbook

from mock.generator import books, expected, generate, write

MOCK = Path(__file__).resolve().parents[2] / "mock"
ROOM = MOCK / "dataroom"


@pytest.fixture(scope="module")
def made() -> books.Books:
    return books.build()


def _trim(rows: list[list]) -> list[list]:
    out = [list(row) for row in rows]
    for row in out:
        while row and row[-1] is None:
            row.pop()
    while out and not out[-1]:
        out.pop()
    return out


def _xlsx(path: Path) -> dict[str, list[list]]:
    book = load_workbook(path)
    return {ws.title: _trim([list(r) for r in ws.iter_rows(values_only=True)]) for ws in book}


def _xls(path: Path) -> dict[str, list[list]]:
    book = xlrd.open_workbook(str(path))
    return {
        ws.name: _trim(
            [[None if c == "" else c for c in ws.row_values(r)] for r in range(ws.nrows)]
        )
        for ws in book.sheets()
    }


def _made(sheets: list, as_float: bool = False) -> dict[str, list[list]]:
    def cell(v: object) -> object:
        return float(v) if as_float and isinstance(v, int | float) else v

    return {s.title: _trim([[cell(v) for v in row] for row in s.rows]) for s in sheets}


@pytest.mark.parametrize(
    "doc", [d for d in generate.DOCUMENTS if d.fmt != "pdf"], ids=lambda d: d.path
)
def test_workbook_holds_what_the_generator_makes(doc: generate.Document, made: books.Books) -> None:
    path = ROOM / doc.path
    if doc.fmt == "xls":
        assert _xls(path) == _made(doc.build(made), as_float=True)
    else:
        assert _xlsx(path) == _made(doc.build(made))


@pytest.mark.parametrize(
    "doc", [d for d in generate.DOCUMENTS if d.fmt == "pdf"], ids=lambda d: d.path
)
def test_pdf_is_what_the_generator_writes(
    doc: generate.Document, made: books.Books, tmp_path: Path
) -> None:
    write.pdf(tmp_path / "out.pdf", doc.build(made))
    assert (ROOM / doc.path).read_bytes() == (tmp_path / "out.pdf").read_bytes()


def test_duplicate_is_byte_identical() -> None:
    for doc in (d for d in generate.DOCUMENTS if d.copy_of):
        assert (ROOM / doc.path).read_bytes() == (ROOM / doc.copy_of).read_bytes()


def test_extras_and_answer_key_are_current(made: books.Books) -> None:
    for name, sheets in generate.EXTRA.items():
        assert _xlsx(MOCK / "extra" / name) == _made(sheets())
    for name, text in expected.extra_texts().items():
        assert (MOCK / "extra" / name).read_text(encoding="utf-8") == text
    assert (MOCK / "EXPECTED.md").read_text(encoding="utf-8") == expected.render(made)


def test_zip_holds_the_dataroom_tree() -> None:
    files = {p.relative_to(ROOM).as_posix(): p for p in ROOM.rglob("*") if p.is_file()}
    assert sorted(files) == sorted(d.path for d in generate.DOCUMENTS)
    with zipfile.ZipFile(MOCK / generate.ZIP) as archive:
        assert sorted(archive.namelist()) == sorted(files)
        for name, path in files.items():
            assert archive.read(name) == path.read_bytes()
            assert datetime(*archive.getinfo(name).date_time) == write.FIXED
