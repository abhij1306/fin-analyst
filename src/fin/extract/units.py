"""Units of money in documents ("₹ in Lakhs", "Rs. in Crores"), shared by the Excel and PDF
extractors. Values are stored in rupees; the unit as written is kept in `raw_values.raw_unit`."""

import re
from decimal import ROUND_HALF_UP, Decimal

RUPEES_PER_UNIT = {
    "rupees": Decimal(1),
    "thousand": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lac": Decimal(100_000),
    "million": Decimal(1_000_000),
    "crore": Decimal(10_000_000),
}
_UNIT = re.compile(
    r"(?:\bin\s+|₹\s*|\brs\.?\s*|\binr\s+)(rupees|thousands?|lakhs?|lacs?|millions?|crores?)\b",
    re.I,
)
_PAISE = Decimal("0.01")


def unit_in(text: str) -> str | None:
    """The unit named in `text` ("lakh", "crore", ...), or None."""
    match = _UNIT.search(text)
    if match is None:
        return None
    word = match.group(1).lower().rstrip("s")
    return "rupees" if word == "rupee" else word


def to_rupees(value: Decimal, unit: str) -> Decimal:
    """`value` in document units as rupees, rounded to the paisa (DECIMAL(20,2))."""
    return (value * RUPEES_PER_UNIT[unit]).quantize(_PAISE, rounding=ROUND_HALF_UP)
