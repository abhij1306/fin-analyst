"""fin.output.escape: text from a document reaches Markdown, HTML and Excel output only escaped."""

from pathlib import Path

from openpyxl import Workbook, load_workbook

from fin.output import escape
from fin.report.tables import table


def test_markdown_cell_keeps_a_table_row_whole() -> None:
    assert escape.markdown_cell("a | b\nc <b>") == "a \\| b c &lt;b&gt;"


def test_a_markdown_link_or_image_stays_text() -> None:
    assert escape.markdown_text("![x](http://e.invalid/a.png) [go](javascript:alert(1)) a\\b") == (
        "!\\[x\\](http://e.invalid/a.png) \\[go\\](javascript:alert(1)) a\\\\b"
    )


def test_markdown_text_and_html_escape_markup() -> None:
    assert escape.markdown_text("<script>") == "&lt;script&gt;"
    assert escape.html('"x" & <y>') == "&quot;x&quot; &amp; &lt;y&gt;"
    assert escape.html(None) == ""


def test_report_tables_escape_every_cell() -> None:
    text = table(["Label"], [["Sales | <i>net</i>"]], "")

    assert "Sales \\| &lt;i&gt;net&lt;/i&gt;" in text


def test_an_excel_formula_from_a_document_is_saved_as_text(tmp_path: Path) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = '=HYPERLINK("http://example.invalid", "x")'
    sheet["A2"] = 5

    escape.excel_book(book).save(tmp_path / "out.xlsx")

    saved = load_workbook(tmp_path / "out.xlsx").active
    assert saved is not None
    assert (saved["A1"].data_type, saved["A2"].value) == ("s", 5)
