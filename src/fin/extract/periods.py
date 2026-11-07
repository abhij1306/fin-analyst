"""Reporting periods, shared by the PDF and Excel extractors and by ingest.

A fact belongs to one period of the company's fiscal year (default April to March):

- `FY25`: the year ended 31 March 2025 (12 months);
- `FY26-Q1` ... `FY26-Q4`: a quarter (3 months; Q1 is April to June);
- `FY26-H1`, `FY26-H2`: a half year (6 months);
- `FY26-9M`: nine months (April to December).

A balance sheet is a point in time: its period is the part of the fiscal year it closes (30 Sep 2025
-> `FY26-H1`, 31 Mar 2025 -> `FY25`). The length of a flow column (profit and loss, cash flow)
comes from its header words: "Quarter ended", "Three months ended", "Half year ended", "Six months
ended", "Nine months ended", "Year ended", or "Year to date" / "Period ended" (from the start of the
fiscal year to the column's date). A flow column whose header names no length is a year only when
it ends on the fiscal year end; otherwise the period is not detected (E202).
"""

import re
from datetime import date

from fin.audit.errors import PeriodsNotDetected

FY_START_MONTH = 4  # Indian fiscal years start in April
# The primary statements, whose lines become facts. A balance sheet is a point in time; the others
# are flows over a period.
STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")
# Month names and abbreviations as printed in column headers, for the PDF and Excel extractors.
MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
YEAR_TO_DATE = 0  # months_in result: from the start of the fiscal year to the column's date

# Header words naming a period length, checked in this order.
_LENGTHS = (
    (re.compile(r"\bnine\s+months?\b", re.I), 9),
    (re.compile(r"\bhalf[\s-]*year|\bsix\s+months?\b", re.I), 6),
    (re.compile(r"\bquarter|\bthree\s+months?\b", re.I), 3),
    (re.compile(r"\byear\s+to\s+date\b|\bperiod\b", re.I), YEAR_TO_DATE),
    (re.compile(r"\byear|\btwelve\s+months?\b", re.I), 12),
)
# A header phrase that only names a length: "Quarter ended", "Half Year Ended", "For the period
# ended", "Three months ended", "Year to date figures for current period ended".
PHRASE = re.compile(
    r"^(?:for\s+the\s+)?(?:(?:current|previous|preceding|corresponding|accounting|financial)\s+)*"
    r"(?:quarter|half[\s-]*year|year|period|(?:three|six|nine|twelve)\s+months?)"
    r"(?:\s+(?:ended|ending|to\s+date|figures))*(?:\s+for\s+current\s+period\s+ended)?"
    r"(?:\s+in\s+the\s+previous\s+year)?\s*:?$",
    re.I,
)


def months_in(text: str) -> int | None:
    """The period length a header names: 3, 6, 9, 12, YEAR_TO_DATE, or None if it names none."""
    for pattern, months in _LENGTHS:
        if pattern.search(text):
            return months
    return None


def months_from_start(period_end: date, start_month: int = FY_START_MONTH) -> int:
    """Months from the start of the fiscal year to `period_end`: 30 Sep -> 6, 31 Mar -> 12."""
    return (period_end.month - start_month) % 12 + 1


def fiscal_year_of(period_end: date, start_month: int = FY_START_MONTH) -> str:
    """'FY25' for the fiscal year that ends in 2025 (31 March 2025 with an April start, 31 December
    2025 with a January start)."""
    ends_next_year = start_month > 1 and period_end.month >= start_month
    end_year = period_end.year + 1 if ends_next_year else period_end.year
    return f"FY{end_year % 100:02d}"


def resolve_months(
    statement: str,
    period_end: date,
    months: int | None,
    start_month: int = FY_START_MONTH,
    *,
    where: str,
) -> int:
    """The length in months of a column: a balance sheet's from its date, a flow column's from its
    header words (`months`). Raises E202 when a flow column names no length and does not end on
    the fiscal year end."""
    elapsed = months_from_start(period_end, start_month)
    if statement == "balance_sheet" or months == YEAR_TO_DATE:
        return elapsed
    if months is None:
        if elapsed == 12:
            return 12
        raise PeriodsNotDetected(
            f"{where}: the column ending {period_end.isoformat()} does not say whether it is a "
            "quarter, half year, nine months or year (no 'Quarter ended' / 'Half year ended' / "
            "'Year ended' above it)"
        )
    return months


def period_of(
    period_end: date, months: int, start_month: int = FY_START_MONTH, *, where: str
) -> str:
    """The period name: 'FY25', 'FY26-Q1', 'FY26-H1', 'FY26-9M'. Raises E202 when the period does
    not line up with the fiscal year (a quarter ending in May, a half year ending in December)."""
    fiscal_year = fiscal_year_of(period_end, start_month)
    elapsed = months_from_start(period_end, start_month)
    name = None
    if months == 12 and elapsed == 12:
        name = fiscal_year
    elif months == 3 and elapsed % 3 == 0:
        name = f"{fiscal_year}-Q{elapsed // 3}"
    elif months == 6 and elapsed % 6 == 0:
        name = f"{fiscal_year}-H{elapsed // 6}"
    elif months == 9 and elapsed == 9:
        name = f"{fiscal_year}-9M"
    if name is None:
        raise PeriodsNotDetected(
            f"{where}: a {months}-month period ending {period_end.isoformat()} does not line up "
            f"with a fiscal year starting in month {start_month}"
        )
    return name
