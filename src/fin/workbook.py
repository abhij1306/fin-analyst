"""`fin export`: a company's standard statements and metrics as one Excel workbook.

Written to `reports/<company>/<company>_financials.xlsx` in the data folder (replaced each time).
Sheets: one per statement and scope ("P&L standalone", "Balance sheet consolidated", ...) in the
databook layout (`fin.databook`): a caption naming the unit in row 1, the column headings in row 2
(panes frozen below them and right of the labels), lines in standard order and indented by depth,
every period side by side (fiscal years first, then quarters and half years, oldest first),
subtotals in bold with a line above, "Unmapped / other" in italics. Then "Metrics": one row per
scope and metric, with its category, unit and definition.

Money is in the display unit (config.toml [display], `fin.display`), converted in SQL; the cell
holds that rounded value, with the display's number format (#,##0.0;(#,##0.0);"–" for brackets).
Earnings per share are as printed; a ratio is a fraction (0.18 = 18%). Each figure's cell comment
gives its source (file and sheet!cell or page) and its marks (stated, corrected, unconfirmed
mapping, read by AI). A total its rounded lines do not add up to is marked *, with a note under
the table. A blank cell has no value: the line is not in that period's documents, or a metric
input is missing.

When the company has a ledger-level trial balance (SPEC-14), "Rebuilt P&L", "Rebuilt balance
sheet" and "Rebuilt cash flow" follow, in the same layout: each line's reported, rebuilt and
difference columns per period (standalone; `fin.rebuilt.compare`), and a Notes column with what a
rebuilt figure needs said (unmapped ledgers under it, a line that is not derivable). A company with
none gets no rebuilt sheets.

With an open engagement, "Differences" comes last: every row of `fin discrepancies`
(`fin.discrepancies.sheet`).
"""

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.worksheet.worksheet import Worksheet

from fin.audit.errors import DataFolderNotWritable, RebuildNotPossible
from fin.config import Display
from fin.databook import databook, period_order
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.discrepancies.sheet import differences_sheet
from fin.display import add_macro, display_settings, excel_format, sql_params
from fin.output import escape
from fin.paths import company_db_path
from fin.rebuilt.compare import flag, rebuilt_rows
from fin.statements import ROUNDING_NOTE, STATEMENTS, statement_rows

SHEET_TITLES = {
    "profit_and_loss": "P&L",
    "balance_sheet": "Balance sheet",
    "cash_flow": "Cash flow",
}
PERCENT_FORMAT = '0.0"%";-0.0"%"'
_TOP = Border(top=Side(style="thin"))

# Money metrics in the display unit (display_value: $per_unit, $decimals), the rest as they are.
_METRICS_SQL = """
SELECT m.scope, m.period, m.period_months, m.period_end, d.category, m.metric, d.unit,
       CASE WHEN d.unit = 'rupees' THEN display_value(m.value, $per_unit, $decimals)
            ELSE m.value END AS value,
       d.definition
FROM metrics m
JOIN metric_definitions d USING (metric)
"""


def export_path(root: Path, company: str) -> Path:
    return root / "reports" / company / f"{company}_financials.xlsx"


def _head(sheet: Worksheet, caption: str, first: list[str], columns: list[str]) -> None:
    """Row 1 the caption (with the unit), row 2 the headings; panes frozen below and right."""
    sheet.append([caption])
    sheet.append(first + columns)
    for cell in (*sheet[1], *sheet[2]):
        cell.font = Font(bold=True)
    sheet.freeze_panes = f"{chr(ord('A') + len(first))}3"
    sheet.column_dimensions["A"].width = 60


def _line_style(sheet: Worksheet, depth: int, subtotal: bool, other: bool) -> None:
    row = sheet[sheet.max_row]
    row[0].alignment = Alignment(indent=depth)
    if other:
        row[0].font = Font(italic=True)
    if subtotal:
        for cell in row:
            cell.font = Font(bold=True)
            cell.border = _TOP


def _comment(cell: dict[str, Any]) -> Comment | None:
    parts = ([f"Source: {cell['source']}"] if cell["source"] else []) + cell["marks"]
    return Comment("; ".join(parts), "fin") if parts else None


def _statement_sheet(book: Workbook, company: str, table: dict[str, Any], display: Display) -> None:
    """One databook table: one row per line, one column per period (and extra), the cells holding
    the SQL-converted values with the display's number format and their source as a comment."""
    title = f"{SHEET_TITLES[table['statement']]} {table['scope']}"
    sheet = book.create_sheet(title)
    caption = f"{company}: {table['title']} ({table['unit_label']})"
    _head(sheet, caption, ["Line"], [c["title"] for c in table["columns"]])
    for line in table["lines"]:
        label = line["label"] + (" *" if line["rounding"] else "")
        sheet.append([label] + [cell["value"] for cell in line["cells"]])
        _line_style(sheet, line["depth"], line["subtotal"], line["other"])
        for target, cell in zip(sheet[sheet.max_row][1:], line["cells"], strict=True):
            if cell["kind"] != "value":
                target.number_format = PERCENT_FORMAT
                continue
            target.number_format = excel_format(display, cell.get("per_share", False))
            if cell["value"] is not None:
                target.comment = _comment(cell)
    if table["rounding"]:
        sheet.append([])
        sheet.append([f"* {ROUNDING_NOTE}"])


def _metrics_sheet(book: Workbook, metrics: list[dict[str, Any]], display: Display) -> None:
    """One row per scope and metric; money metrics in the display unit."""
    sheet = book.create_sheet("Metrics")
    periods = period_order(metrics)
    sheet.append(["Scope", "Category", "Metric", "Unit", *periods, "Definition"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "E2"
    table: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for m in metrics:
        shown = display.unit_label if m["unit"] == "rupees" else m["unit"]
        key = (m["scope"], m["category"], m["metric"], shown, m["definition"])
        table.setdefault(key, {})[m["period"]] = m["value"]
    for (scope, category, metric, unit, definition), values in sorted(table.items()):
        sheet.append([scope, category, metric, unit, *(values.get(p) for p in periods), definition])
        if unit == display.unit_label:
            for cell in sheet[sheet.max_row][4 : 4 + len(periods)]:
                cell.number_format = excel_format(display)
    sheet.column_dimensions["C"].width = 26


def _rebuilt_sheet(
    book: Workbook, company: str, statement: str, rows: list[dict[str, Any]], display: Display
) -> None:
    """One statement: per line, each period's reported, rebuilt and difference, then the notes."""
    periods = period_order(rows)
    title = f"Rebuilt {SHEET_TITLES[statement]}"
    sheet = book.create_sheet(title)
    columns = [f"{p} {what}" for p in periods for what in ("reported", "rebuilt", "difference")]
    caption = f"{company}: {title}, standalone ({display.unit_label})"
    _head(sheet, caption, ["Line"], [*columns, "Notes"])
    order: dict[tuple[int, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["line_no"], r["label"])
        entry = order.setdefault(key, {"depth": r["depth"], "notes": [], "subtotal": False})
        entry["subtotal"] |= bool(r["subtotal"])
        entry["per_share"] = r["unit"] != "rupees"
        for what in ("reported", "rebuilt", "difference"):
            entry[f"{r['period']} {what}"] = r[f"{what}_shown"]
        if note := flag(r, display):
            entry["notes"].append(f"{r['period']}: {note}")
    for (_, label), entry in sorted(order.items()):
        sheet.append([label] + [entry.get(c) for c in columns] + ["; ".join(entry["notes"])])
        _line_style(sheet, entry["depth"], entry["subtotal"], False)
        for cell in sheet[sheet.max_row][1 : len(columns) + 1]:
            cell.number_format = excel_format(display, entry["per_share"])


def _rebuilt_sheets(book: Workbook, root: Path, company: str, display: Display) -> None:
    try:
        result = rebuilt_rows(root, company, display=display)
    except RebuildNotPossible:
        return  # no ledger-level trial balance: nothing rebuilt to show
    for statement in STATEMENTS:
        rows = [r for r in result["rows"] if r["statement"] == statement]
        if rows:
            _rebuilt_sheet(book, company, statement, rows, display)


def export_workbook(root: Path, company: str, display: Display | None = None) -> Path:
    """Write the workbook in `display` (default: the company's display settings) and return its
    path. Raises E601 (`statement_rows`) when the company has no figures."""
    display = display or display_settings(root, company)
    lines = statement_rows(root, company, display=display)
    with connect(company_db_path(root, company)) as con:
        add_macro(con)
        metrics = fetch_dicts(con, _METRICS_SQL, sql_params(display))

    book = Workbook()
    first = book.active
    if first is not None:
        book.remove(first)
    for table in databook(lines, display):
        _statement_sheet(book, company, table, display)
    _metrics_sheet(book, metrics, display)
    _rebuilt_sheets(book, root, company, display)
    differences_sheet(book, root, company, display)

    path = export_path(root, company)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        escape.excel_book(book).save(path)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path} (is it open in Excel?): {exc}") from exc
    return path
