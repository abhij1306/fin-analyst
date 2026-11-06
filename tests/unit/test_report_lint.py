"""The narrative lint (E602): any digit outside a reference or an FYnn label is a typed number."""

import pytest

from fin.audit.errors import NarrativeHasTypedNumbers
from fin.report.lint import lint, typed_number_lines

PLANTED = """\
## Growth

Revenue was {{fact:revenue:FY25}} in FY25.
Revenue grew 12% in FY25.

## Observations

<!-- Comments may hold digits: 2024-25, 18% -->
1. Receivable days rose.
- Debt was {{metric:debt_to_equity:FY25}} of equity, up from 0.4 in FY2024.
"""


def test_planted_numbers_raise_e602_listing_each_line() -> None:
    with pytest.raises(NarrativeHasTypedNumbers) as raised:
        lint(PLANTED, "reports/kestrel/standalone/narrative.md")
    assert str(raised.value) == (
        "E602: reports/kestrel/standalone/narrative.md has hand-typed numbers on 3 line(s); "
        "write each number as a reference such as {{metric:ebitda_margin:FY25}} or "
        "{{fact:revenue:FY25}}:\n"
        "  line 4: Revenue grew 12% in FY25.\n"
        "  line 9: 1. Receivable days rose.\n"
        "  line 10: - Debt was {{metric:debt_to_equity:FY25}} of equity, up from 0.4 in FY2024."
    )


def test_references_fiscal_years_and_comments_pass() -> None:
    text = (
        "<!--\nYears: FY23, FY24\n-->\n## Profitability\n\n"
        "EBITDA margin was {{metric:ebitda_margin:FY23}} in FY23 and "
        "{{metric:ebitda_margin:FY25}} in FY25.\n"
    )
    lint(text, "narrative.md")
    assert typed_number_lines(text) == []


@pytest.mark.parametrize(
    "line",
    [
        "Revenue was ₹ one hundred and 5 crores.",
        "{{metric:ebitda_margin:2025}}",  # not a reference: the year must be FYnn
        "{{metric:ebitda_margin:FY25}}0",  # digit right after a reference
        "In FY2025 revenue rose.",  # only two-digit FY labels are allowed
        "Q3 revenue rose.",
        "Revenue rose by ٥ percent.",  # any Unicode digit
    ],
)
def test_other_digits_are_typed_numbers(line: str) -> None:
    assert typed_number_lines(f"## Growth\n{line}\n") == [(2, line)]
