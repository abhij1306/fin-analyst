"""Open an old Excel workbook (.xls) as an openpyxl workbook, so `fin.extract.excel` and the
column-profile readers read it like a .xlsx (docs/specs/SPEC-04 Q2).

xlrd reads the file; nothing in it is run (an .xls holds no macros xlrd would run, and it never
evaluates formulas). Each cell's value is the one Excel saved: numbers, text, dates (as datetimes),
TRUE/FALSE; an error cell (#N/A) keeps its text. Merged ranges are kept, so a heading merged over
value columns covers them as in a .xlsx."""

from datetime import datetime
from pathlib import Path
from typing import Any

import xlrd
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from xlrd.compdoc import CompDocError

from fin.audit.errors import FileEncrypted, FileUnreadable


def open_xls(path: Path) -> Workbook:
    """The .xls workbook at `path` as an openpyxl workbook. E102 when it is password protected,
    E101 when it cannot be read."""
    try:
        source = xlrd.open_workbook(str(path), formatting_info=True, on_demand=False)
    except xlrd.XLRDError as exc:
        if "encrypted" in str(exc).lower():
            raise FileEncrypted(f"{path.name}: the workbook is password protected") from exc
        raise FileUnreadable(f"{path.name}: not a readable .xls workbook ({exc})") from exc
    except (OSError, CompDocError, AssertionError, ValueError, IndexError) as exc:
        raise FileUnreadable(f"{path.name}: not a readable .xls workbook ({exc})") from exc
    book = Workbook()
    book.remove(book.worksheets[0])
    for sheet in source.sheets():
        _copy_sheet(sheet, book.create_sheet(sheet.name), source.datemode)
    return book


def _copy_sheet(sheet: xlrd.sheet.Sheet, target: Worksheet, datemode: int) -> None:
    """Copy one sheet's saved values and merged ranges into `target`."""
    for row in range(sheet.nrows):
        for column in range(sheet.ncols):
            value = _value(sheet.cell(row, column), datemode)
            if value is not None:
                target.cell(row + 1, column + 1, value)
    for first_row, end_row, first_column, end_column in sheet.merged_cells:
        target.merge_cells(
            start_row=first_row + 1,
            end_row=end_row,
            start_column=first_column + 1,
            end_column=end_column,
        )


def _value(cell: xlrd.sheet.Cell, datemode: int) -> Any:
    """A cell's saved value as openpyxl would hold it; None for a blank cell."""
    kind = cell.ctype
    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if kind == xlrd.XL_CELL_DATE:
        return datetime(*xlrd.xldate_as_tuple(cell.value, datemode))
    if kind == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if kind == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value, "#ERROR")
    if kind == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
        return int(cell.value)  # Excel stores every number as a float: 1200 is read as 1200
    return cell.value
