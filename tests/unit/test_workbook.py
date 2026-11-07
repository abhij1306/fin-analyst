"""A statement sheet shows every column in the sheet's unit, whatever unit each document used."""

from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook

from fin.workbook import _statement_sheet


def row(period: str, end: date, unit: str, value: str, label: str = "Revenue") -> dict[str, Any]:
    return {
        "period": period,
        "period_months": 12,
        "period_end": end,
        "line_no": 1 if label == "Revenue" else 2,
        "label": label,
        "depth": 0,
        "unit": unit,
        "value": Decimal(value),
    }


def test_documents_in_lakh_and_crore_show_in_the_sheet_unit() -> None:
    book = Workbook()
    rows = [
        row("FY23", date(2023, 3, 31), "lakh", "1000000000"),
        row("FY24", date(2024, 3, 31), "crore", "1000000000"),
        row("FY24", date(2024, 3, 31), "rupees_per_share", "12.5", "Basic EPS"),
    ]

    _statement_sheet(book, "P&L", rows, "lakh")

    values = [[c.value for c in r] for r in book["P&L"].iter_rows()]
    assert values == [
        ["Line (₹ in lakh)", "FY23", "FY24"],
        ["Revenue", Decimal("10000.00"), Decimal("10000.00")],
        ["Basic EPS", None, Decimal("12.5")],
    ]
