"""`fin export`: a company's standard statements and metrics as one Excel workbook.

Written to `reports/<company>/<company>_financials.xlsx` in the data folder (replaced each time).
Sheets: one per statement and scope ("P&L standalone", "Balance sheet consolidated", ...), lines in
standard order and indented by depth, every period side by side (fiscal years first, then quarters
and half years, oldest first); then "Metrics": one row per scope and metric, with its category,
unit and definition. Money is in the unit the company's documents use (lakh, crore, ...; named in
the header and the Unit column), two decimals; earnings per share as printed; a ratio is a fraction
(0.18 = 18%). A blank cell has no value: the line is not in that period's documents, or a metric
input is missing.
"""

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from fin.audit.errors import DataFolderNotWritable
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.paths import company_db_path
from fin.statements import (
    OTHER_LABEL,
    PER_SHARE,
    STATEMENTS,
    in_unit,
    statement_rows,
    unit_title,
)

SHEET_TITLES = {
    "profit_and_loss": "P&L",
    "balance_sheet": "Balance sheet",
    "cash_flow": "Cash flow",
}
MONEY_FORMAT = "#,##0.00"

_METRICS_SQL = """
SELECT m.scope, m.period, m.period_months, m.period_end, d.category, m.metric, d.unit, m.value,
       d.definition
FROM metrics m
JOIN metric_definitions d USING (metric)
"""


def export_path(root: Path, company: str) -> Path:
    return root / "reports" / company / f"{company}_financials.xlsx"


def _periods(rows: list[dict[str, Any]]) -> list[str]:
    """Periods in column order: fiscal years, then other periods; oldest first."""
    order = {
        r["period"]: (r["period_months"] != 12, r["period_end"], -r["period_months"]) for r in rows
    }
    return sorted(order, key=lambda p: order[p])


def _header(sheet: Any, first: list[str], periods: list[str]) -> None:
    sheet.append(first + periods)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = f"{get_column_letter(len(first) + 1)}2"


def _statement_sheet(book: Workbook, title: str, rows: list[dict[str, Any]], unit: str) -> None:
    """One statement and scope: one row per line (an account, or the "Unmapped / other" line
    under a subtotal), one column per period, in `unit`."""
    periods = _periods(rows)
    sheet = book.create_sheet(title)
    _header(sheet, [f"Line ({unit_title(unit)})"], periods)
    order: dict[tuple[int, str], dict[str, Any]] = {}
    for r in rows:
        entry = order.setdefault((r["line_no"], r["label"]), {"depth": r["depth"]})
        entry[r["period"]] = in_unit(r["value"], PER_SHARE if r["unit"] == PER_SHARE else unit)
    for (_, label), entry in sorted(order.items()):
        sheet.append([label] + [entry.get(p) for p in periods])
        first = sheet.cell(row=sheet.max_row, column=1)
        first.alignment = Alignment(indent=entry["depth"])
        if label == OTHER_LABEL:
            first.font = Font(italic=True)
        for cell in sheet[sheet.max_row][1:]:
            cell.number_format = MONEY_FORMAT
    sheet.column_dimensions["A"].width = 60


def _metrics_sheet(book: Workbook, metrics: list[dict[str, Any]], unit: str) -> None:
    """One row per scope and metric; money metrics in `unit`."""
    sheet = book.create_sheet("Metrics")
    periods = _periods(metrics)
    _header(sheet, ["Scope", "Category", "Metric", "Unit"], periods + ["Definition"])
    table: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for m in metrics:
        money = m["unit"] == "rupees"
        shown = unit_title(unit) if money else m["unit"]
        key = (m["scope"], m["category"], m["metric"], shown, m["definition"])
        value = m["value"]
        if money and value is not None:
            value = in_unit(value, unit)
        table.setdefault(key, {})[m["period"]] = value
    for (scope, category, metric, unit, definition), values in sorted(table.items()):
        cells = [values.get(p) for p in periods]
        sheet.append([scope, category, metric, unit, *cells, definition])
    sheet.column_dimensions["C"].width = 26


def export_workbook(root: Path, company: str) -> Path:
    """Write the workbook and return its path. Raises E601 (`statement_rows`) when the company
    has no figures."""
    lines = statement_rows(root, company)
    with connect(company_db_path(root, company)) as con:
        metrics = fetch_dicts(con, _METRICS_SQL)

    unit = next((r["unit"] for r in lines if r["unit"] != PER_SHARE), "rupees")
    book = Workbook()
    book.remove(book.active)
    for scope in sorted({r["scope"] for r in lines}):
        for statement in STATEMENTS:
            rows = [r for r in lines if r["scope"] == scope and r["statement"] == statement]
            if rows:
                _statement_sheet(book, f"{SHEET_TITLES[statement]} {scope}", rows, unit)
    _metrics_sheet(book, metrics, unit)

    path = export_path(root, company)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        book.save(path)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path} (is it open in Excel?): {exc}") from exc
    return path
