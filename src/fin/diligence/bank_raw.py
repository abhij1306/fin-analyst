"""What a bank statement file holds before it is interpreted (`fin.diligence.bank`)."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from fin.extract.profiles import Profile


@dataclass(frozen=True)
class RawRow:
    sheet: str  # the Excel sheet, or "page N"
    page: int | None  # the PDF page
    row: int  # the sheet row, or the row of the page's tables (the saved parse)
    cells: dict[str, Any]  # field -> cell value (text for a PDF)


# A PDF parse: page number -> {"tables": [grid of text], "texts": [lines]}.
Pages = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Raw:
    profile: Profile
    start: date
    end: date
    lines: list[str]  # header and summary text lines (account, opening balance, ...)
    rows: list[RawRow]
    reader: str  # 'excel', or the PDF add-on's name
    parse_path: str | None  # a PDF's saved parse, relative to the data folder
