"""The pure parts of fin.diligence: periods, amounts, query status changes, finding sources."""

from decimal import Decimal

import pytest

from fin.diligence.engagement import parse_periods, parse_rupees, rupees
from fin.diligence.queries import allowed, finding_sources


def test_periods_are_annual_each_once_and_sorted() -> None:
    assert parse_periods(" FY26,FY24 , FY25") == ["FY24", "FY25", "FY26"]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "give at least one annual period, e.g. FY24,FY25,FY26"),
        ("FY25,FY26-Q1", "not an annual period: FY26-Q1 (use e.g. FY24,FY25,FY26)"),
        ("FY25,FY25", "a period is given more than once: FY25,FY25"),
    ],
)
def test_bad_periods_are_refused(text: str, problem: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_periods(text)
    assert str(caught.value) == problem


def test_amounts_are_exact_rupees() -> None:
    assert parse_rupees("-350000.5") == Decimal("-350000.50")
    assert parse_rupees("1e5") == Decimal("100000")


@pytest.mark.parametrize("text", ["1.005", "abc", "NaN", "Infinity"])
def test_amounts_that_would_be_rounded_or_are_not_numbers_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="is not an amount in rupees"):
        parse_rupees(text)


def test_amounts_display_with_sign_before_the_rupee() -> None:
    assert rupees(Decimal("-35000000.00")) == "-₹35,000,000.00"
    assert rupees(Decimal("1250.5")) == "₹1,250.5"
    assert rupees(None) == "-"


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ("draft", "approved", True),
        ("approved", "sent", True),
        ("sent", "answered", True),
        ("answered", "closed", True),
        ("answered", "withdrawn", True),
        ("draft", "sent", False),
        ("approved", "approved", False),
        ("sent", "closed", False),
        ("closed", "withdrawn", False),
        ("withdrawn", "withdrawn", False),
        ("draft", "draft", False),
    ],
)
def test_query_status_changes(current: str, target: str, expected: bool) -> None:
    assert allowed(current, target) is expected


def test_finding_sources_name_both_files_and_cells() -> None:
    finding = {
        "finding_id": 3,
        "file_name": "b.xlsx",
        "fin_source": "P&L!C7",
        "other_file": "a.xlsx",
        "other_source": "P&L!B7",
    }
    assert finding_sources(finding) == "b.xlsx P&L!C7; a.xlsx P&L!B7"
    # A check within one document (E401, E402): the other figure is in the same file.
    assert finding_sources(finding | {"other_file": None}) == "b.xlsx P&L!C7; b.xlsx P&L!B7"
    assert finding_sources(finding | {"fin_source": None, "other_source": None}) == "b.xlsx"
