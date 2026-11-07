from decimal import Decimal

import pytest

from fin.extract.units import parse_figure, to_rupees, unit_in


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("(₹ in Lakhs)", "lakh"),
        ("(Rs. in Millions, except per equity share data)", "million"),
        ("Rs. In Lacs except for per share data", "lac"),
        # A unit word without "in", a rupee sign or "Rs" before it is not read as a unit.
        ("a: Crores", None),
        ("Crores of shares were issued in the year", None),
        ("Particulars", None),
        ("(INR crores)", "crore"),
        ("(Amount in thousands)", "thousand"),
        ("(in rupees)", "rupees"),
        ("(in rupee)", None),
        ("(in lakhss)", None),
    ],
)
def test_unit_in(text: str, expected: str | None) -> None:
    assert unit_in(text) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("0.005"), "rupees", "0.01"),  # half a paisa rounds up
        (Decimal("0.015"), "rupees", "0.02"),
        (Decimal("-0.005"), "rupees", "-0.01"),
        (Decimal("0.0049"), "rupees", "0.00"),
        (Decimal("28.50000005"), "lakh", "2850000.01"),
        (Decimal("28.50000005"), 100_000, "2850000.01"),  # given as rupees per unit
    ],
)
def test_to_rupees_rounds_half_up_to_the_paisa(
    value: Decimal, unit: str | int, expected: str
) -> None:
    assert str(to_rupees(value, unit)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.56", Decimal("1234.56")),
        ("(56.67)", Decimal("-56.67")),
        ("-56.67", Decimal("-56.67")),
        ("(-5)", Decimal("-5")),
        (".82", Decimal("0.82")),
        ("12,34,567", Decimal("1234567")),
        (" 13409.29 ", Decimal("13409.29")),
        ("-", Decimal(0)),
        ("–", Decimal(0)),
        ("—", Decimal(0)),
        ("Nil", Decimal(0)),
        ("( - 5 )", Decimal("-5")),
        ("(1,234)", Decimal("-1234")),
        ("- -5", None),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_figure(text: str, expected: Decimal | None) -> None:
    assert parse_figure(text) == expected
