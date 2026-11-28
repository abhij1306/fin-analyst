"""How money is shown (fin.display): the display_value SQL macro converts exactly and rounds half
away from zero; the text and Excel formats only format what SQL returned."""

from decimal import Decimal as D

import pytest

from fin.config import Display
from fin.display import convert, excel_format, money, number, percent

MN = Display("millions", 1, "brackets", "INR Mn")
CR = Display("crores", 2, "minus", "Cr")


@pytest.mark.parametrize(
    ("value", "display", "shown"),
    [
        ("1234567.89", MN, "1.2"),
        ("1250000", MN, "1.3"),  # half away from zero
        ("-1250000", MN, "-1.3"),
        ("1249999.99", MN, "1.2"),
        ("0.01", MN, "0.0"),
        ("1340912345.67", CR, "134.09"),
        ("123456789012345678.99", CR, "12345678901.23"),  # past a double's 15 digits, exactly
        ("150000", Display("lakhs", 0, "minus", "lakh"), "2"),
        ("1234.565", Display("rupees", 2, "minus", "INR"), "1234.57"),
    ],
)
def test_the_macro_converts_exactly(value: str, display: Display, shown: str) -> None:
    assert convert([D(value)], display) == [D(shown)]


def test_none_stays_none_and_per_share_is_not_scaled() -> None:
    assert convert([None, D("2500000")], MN) == [None, D("2.5")]
    assert convert([D("12.345")], CR, per_share=True) == [D("12.35")]


def test_text_formats() -> None:
    assert number(D("-1234.5"), MN) == "(1,234.5)"
    assert number(D("0"), MN) == "–"
    assert number(D("-1234.5"), CR) == "-1,234.50"
    assert number(D("8"), MN, per_share=True) == "8.00"
    assert number(None, MN) == ""
    assert money(D("-1250000"), MN) == "(1.3) INR Mn"
    assert money(D("1340912345.67"), CR) == "134.09 Cr"
    assert money(None, MN) == "n/a"
    assert percent(D("1234.5")) == "1,234.5%"  # SQL rounded it already


def test_excel_formats() -> None:
    assert excel_format(MN) == '#,##0.0;(#,##0.0);"–"'
    assert excel_format(CR) == "#,##0.00;-#,##0.00"
    assert excel_format(Display("lakhs", 0, "brackets", "lakh")) == '#,##0;(#,##0);"–"'
    assert excel_format(MN, per_share=True) == '#,##0.00;(#,##0.00);"–"'
