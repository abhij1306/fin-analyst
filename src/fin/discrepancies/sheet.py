"""The "Differences" sheet of `fin export` (SPEC-15): every row of `fin discrepancies`, ranked
by amount, money in the display unit as SQL converted it. A company with no open engagement or
no differences gets no sheet. Text from documents (keys, sources, notes) is written as text, never
as a formula."""

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from fin.audit.errors import EngagementState
from fin.config import Display
from fin.discrepancies.rows import discrepancy_rows, query_text
from fin.display import excel_format

TITLE = "Differences"
COLUMNS = [
    ("Line", "row_id"),
    ("Under", "parent_id"),
    ("Level", "level"),
    ("Period", "period"),
    ("Reconciliation", "reconciliation"),
    ("What", "key"),
    ("Kind", "kind"),
    ("First", "left_name"),
    ("First amount", "left_amount_shown"),
    ("Second", "right_name"),
    ("Second amount", "right_amount_shown"),
    ("Difference", "difference_shown"),
    ("Explained below", "explained_shown"),
    ("Unexplained", "unexplained_shown"),
    ("Over the threshold", "over_threshold"),
    ("Currency", "currency"),
    ("First source", "left_source"),
    ("Second source", "right_source"),
    ("Note", "note"),
]
MONEY = {"left_amount_shown", "right_amount_shown", "difference_shown", "explained_shown",
         "unexplained_shown"}  # fmt: skip


def _value(row: dict[str, Any], key: str) -> Any:
    if key == "row_id":
        return row["item_id"] if row["item_id"] is not None else row["row_id"]  # ref-<n>
    if key in ("left_amount_shown", "right_amount_shown") and row["currency"]:
        return row[key.removesuffix("_shown")]  # in its currency, as stored
    return row[key]


def differences_sheet(book: Workbook, root: Path, company: str, display: Display) -> None:
    """Add the sheet (none without an open engagement or without differences)."""
    try:
        rows = discrepancy_rows(root, company, display=display)["rows"]
    except EngagementState:
        return
    if not rows:
        return
    sheet = book.create_sheet(TITLE)
    sheet.append([f"{company}: differences, ranked by amount ({display.unit_label})"])
    sheet.append([title for title, _ in COLUMNS] + ["Query"])
    for cell in (*sheet[1], *sheet[2]):
        cell.font = Font(bold=True)
    sheet.freeze_panes = "C3"
    for row in rows:
        sheet.append([_value(row, key) for _, key in COLUMNS] + [query_text(row)])
        for cell, (_, key) in zip(sheet[sheet.max_row], COLUMNS, strict=False):
            if isinstance(cell.value, str):
                cell.data_type = "s"  # a key such as "=1+1" stays text
            elif key in MONEY and not row["currency"]:
                cell.number_format = excel_format(display)
        sheet[sheet.max_row][-1].data_type = "s"
