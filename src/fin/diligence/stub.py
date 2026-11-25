"""The current-year stub of an engagement (`fin engagement open ... --stub FY27-YTD-SEP`).

A stub is a year-to-date period after the last annual period in scope, built from unaudited
management data (its trial balance and Ledger Vouchers). It is named by its fiscal year and end
month: `FY27-YTD-SEP` runs from the first day of FY27 to the last day of September in it. It must
be the fiscal year right after the last annual period (anchored to the last audited year) and end
before that year's last month (a full year is an annual period). Its figures are labelled
`UNAUDITED` wherever they show and are never annualised: base figures and growth use the audited
years only.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date

from fin.extract.periods import MONTHS, months_from_start

UNAUDITED = "unaudited management accounts"
STUB = re.compile(r"^FY(\d{2})-YTD-([A-Z]{3})$")
ABBREVIATIONS = {number: name.upper() for name, number in MONTHS.items() if len(name) == 3}


@dataclass(frozen=True)
class Stub:
    name: str
    start: date
    end: date


def fiscal_start(year: int, start_month: int) -> date:
    """The first day of the fiscal year FY<year> (a four-digit year, e.g. 2027)."""
    return date(year if start_month == 1 else year - 1, start_month, 1)


def parse_stub(text: str, periods: list[str], start_month: int) -> Stub:
    """'FY27-YTD-SEP' -> Stub('FY27-YTD-SEP', 2026-04-01, 2026-09-30) with an April start. Raises
    ValueError unless it names a month before the fiscal year's last, in the fiscal year right
    after the last of `periods` (annual periods, sorted)."""
    match = STUB.match(text.strip().upper())
    month = None
    if match:
        month = next((n for n, name in ABBREVIATIONS.items() if name == match[2]), None)
    if match is None or month is None:
        raise ValueError(
            f"{text!r} is not a stub period; name it by its fiscal year and end month, e.g. "
            "FY27-YTD-SEP"
        )
    year = 2000 + int(match[1])
    last = 2000 + int(periods[-1][2:])
    if year != last + 1:
        raise ValueError(
            f"the stub {match[0]} must be in the fiscal year right after the last annual period "
            f"{periods[-1]} (FY{(last + 1) % 100:02d})"
        )
    start = fiscal_start(year, start_month)
    end_year = start.year if month >= start_month else start.year + 1
    end = date(end_year, month, calendar.monthrange(end_year, month)[1])
    if months_from_start(end, start_month) == 12:
        raise ValueError(
            f"the stub {match[0]} ends on the fiscal year's last month: that is the annual "
            f"period FY{year % 100:02d}; give it in --periods"
        )
    return Stub(match[0], start, end)
