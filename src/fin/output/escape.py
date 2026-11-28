"""Escaping at output (docs/engineering.md rule 7): text from a document, the user or Claude
reaches Markdown, HTML and Excel only through these functions, one per target.
tests/unit/test_output_escape.py fails when a module escapes on its own or saves a workbook without
`excel_book`."""

import html as _html

from openpyxl.cell.cell import Cell
from openpyxl.workbook import Workbook


def html(text: object) -> str:
    """Text for HTML content or a quoted attribute: & < > " and ' escaped. None is empty."""
    return _html.escape("" if text is None else str(text), quote=True)


def markdown_text(text: object) -> str:
    """Free text for a Markdown paragraph that becomes HTML: markup is escaped, so it shows as text
    and never runs in the rendered page. None is empty."""
    return _html.escape("" if text is None else str(text), quote=False)


def markdown_cell(text: object) -> str:
    """One Markdown table cell: on one line (a newline would end the row), markup escaped, and a
    pipe escaped so it cannot split the cell. None is empty."""
    return " ".join(markdown_text(text).split()).replace("|", "\\|")


def excel_cell(cell: Cell) -> None:
    """Keep a cell's text as text. openpyxl stores a string that starts with "=" as a formula, so
    text from a document would run in Excel (formula injection); fin writes no formulas."""
    if cell.data_type == "f":
        cell.data_type = "s"


def excel_book(book: Workbook) -> Workbook:
    """Every cell of every sheet through `excel_cell`; call it just before saving a workbook."""
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell, Cell):  # a merged cell holds no value
                    excel_cell(cell)
    return book
