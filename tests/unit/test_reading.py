"""Reading files (`fin.extract.reading`): figures Claude read, as a CSV."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fin.audit.errors import FinError
from fin.extract.reading import printed_value, read_reading

HEADER = "statement,scope,period_end,period_months,unit,section,label,value_as_printed,source\n"


def reading(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "r.csv"
    path.write_text(HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234.56", Decimal("1234.56")),
        ("(56.67)", Decimal("-56.67")),
        ("-12.5", Decimal("-12.5")),
        (".82", Decimal("0.82")),
        ("-", Decimal(0)),
        ("—", Decimal(0)),
        ("Nil", Decimal(0)),
        ("12,34,567", Decimal("1234567")),
    ],
)
def test_printed_value(text: str, value: Decimal) -> None:
    assert printed_value(text, "here") == value


def test_lines_get_period_rupees_and_source(tmp_path: Path) -> None:
    path = reading(
        tmp_path,
        'balance_sheet,standalone,2025-09-30,,(₹ in Lakhs),Current assets,Inventories,"1,200.50",'
        "page 7",
        'profit_and_loss,consolidated,2025-06-30,3,Rs. in Crores,,"Revenue, net",(3.25),P&L!C12',
    )

    first, second = read_reading(path, 4)

    assert (first.period, first.period_months) == ("FY26-H1", 6)
    assert first.period_end == date(2025, 9, 30)
    assert (first.rupees, first.page, first.source) == (Decimal("120050000.00"), 7, "page 7")
    assert (second.label, second.period, second.rupees) == (
        "Revenue, net",
        "FY26-Q1",
        Decimal("-32500000.00"),
    )
    assert (second.sheet, second.cell, second.source) == ("P&L", "C12", "P&L!C12")


@pytest.mark.parametrize(
    ("row", "error"),
    [
        (
            "notes,standalone,2025-03-31,,in lakhs,,Cash,1.00,3",
            "E205: r.csv line 2: statement 'notes' is not one of",
        ),
        ("balance_sheet,group,2025-03-31,,in lakhs,,Cash,1.00,3", "E205: r.csv line 2: scope"),
        (
            "balance_sheet,standalone,31/03/2025,,in lakhs,,Cash,1.00,3",
            "E205: r.csv line 2: period_end '31/03/2025' is not YYYY-MM-DD",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,amounts,,Cash,1.00,3",
            "E203: r.csv line 2: no unit in 'amounts'",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.0O,3",
            "E205: r.csv line 2: '1.0O' is not a figure as printed",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,top of page",
            "E205: r.csv line 2: source 'top of page' is not a page number or Sheet!Cell",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,page 0",
            "E205: r.csv line 2: source 'page 0' is not a page number or Sheet!Cell",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,BS!C0",
            "E205: r.csv line 2: source 'BS!C0' is not a page number or Sheet!Cell",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,BS!XFE1",
            "E205: r.csv line 2: source 'BS!XFE1' is not a page number or Sheet!Cell",
        ),
        (
            "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,BS!A1048577",
            "E205: r.csv line 2: source 'BS!A1048577' is not a page number or Sheet!Cell",
        ),
        (
            "profit_and_loss,standalone,2025-12-31,6,in lakhs,,Revenue,1.00,3",
            "E202: r.csv line 2: a 6-month period ending 2025-12-31 does not line up",
        ),
    ],
)
def test_bad_lines_name_the_line_and_code(tmp_path: Path, row: str, error: str) -> None:
    path = reading(tmp_path, row)
    with pytest.raises(FinError) as raised:
        read_reading(path, 4)
    assert str(raised.value).startswith(error)


def test_last_cell_of_a_worksheet_is_a_source(tmp_path: Path) -> None:
    row = "balance_sheet,standalone,2025-03-31,,in lakhs,,Cash,1.00,BS!XFD1048576"
    path = reading(tmp_path, row)
    (line,) = read_reading(path, 4)
    assert line.source == "BS!XFD1048576"


def test_empty_reading_is_e205(tmp_path: Path) -> None:
    path = reading(tmp_path)
    with pytest.raises(FinError, match="the reading has no lines") as raised:
        read_reading(path, 4)
    assert raised.value.code == "E205"
