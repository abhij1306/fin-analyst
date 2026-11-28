"""An exported statement sheet is the databook layout: a caption with the unit, the headings, frozen
panes, values as SQL converted them with the display's number format, bold subtotals, and each
figure's source and marks in a comment."""

from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook

from fin.config import Display
from fin.databook import databook
from fin.workbook import _statement_sheet

MN = Display("millions", 1, "brackets", "INR Mn")


def row(period: str, line_no: int, label: str, shown: str, **more: Any) -> dict[str, Any]:
    return {
        "scope": "standalone",
        "statement": "profit_and_loss",
        "period": period,
        "period_months": 12,
        "period_end": date(int(period[2:]) + 2000, 3, 31),
        "line_no": line_no,
        "label": label,
        "depth": 0,
        "unit": "rupees",
        "shown": Decimal(shown),
        "subtotal": False,
        "rounding": False,
        "source": f"fs_{period}.xlsx P&L!C{line_no}",
        "of_revenue": None,
        "yoy": None,
    } | more


def test_a_sheet_is_the_databook_layout() -> None:
    rows = [
        row("FY25", 1, "Revenue", "2.5", stated_id=3),
        row("FY24", 1, "Revenue", "2.0"),
        row("FY25", 9, "Profit for the year", "-0.3", subtotal=True),
        row("FY25", 10, "Basic EPS", "8.00", unit="rupees_per_share"),
    ]
    [table] = databook(rows, MN)
    book = Workbook()

    _statement_sheet(book, "acme", table, MN)

    sheet = book["P&L standalone"]
    values = [[c.value for c in r] for r in sheet.iter_rows()]
    assert values == [
        ["acme: Profit and loss, standalone (INR Mn)", None, None],
        ["Line", "FY24", "FY25"],
        ["Revenue", Decimal("2.0"), Decimal("2.5")],
        ["Profit for the year", None, Decimal("-0.3")],
        ["Basic EPS", None, Decimal("8.00")],
    ]
    assert sheet.freeze_panes == "B3"
    assert sheet["C3"].number_format == '#,##0.0;(#,##0.0);"–"'
    assert sheet["C5"].number_format == '#,##0.00;(#,##0.00);"–"'
    assert sheet["C3"].comment.text == "Source: fs_FY25.xlsx P&L!C1; stated by the user, #3"
    assert sheet["A4"].font.bold
    assert not sheet["A3"].font.bold
