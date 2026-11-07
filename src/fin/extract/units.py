"""Units of money and printed figures, shared by the Excel, PDF and reading extractors and by
ingest and verify. Values are stored in rupees; the unit as written is kept in
`raw_values.raw_unit`."""

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
    r"(?:\binr?\s+|₹\s*|\brs\.?\s*)(rupees|(?:thousand|lakh|lac|million|crore)s?)\b",
    re.I,
)
_PAISE = Decimal("0.01")

# A figure printed as nil: a lone dash or the word "nil" (any case).
NIL = ("-", "–", "—", "nil")
# '1,234.56', '(56.67)', '-56.67', '.82'; paired brackets or a minus mean negative.
_FIGURE = re.compile(r"^([({])?\s*(?:(-)\s*)?([\d,]*\d(?:\.\d+)?|\.\d+)\s*(\))?$")


def unit_in(text: str) -> str | None:
    """The unit named in `text` ("lakh", "crore", ...), or None."""
    match = _UNIT.search(text)
    if match is None:
        return None
    word = match.group(1).lower().rstrip("s")
    return "rupees" if word == "rupee" else word


def rupees_per_unit(unit: str) -> int:
    """Rupees in one `unit`: 'lakh' -> 100000."""
    return int(RUPEES_PER_UNIT[unit])


def to_rupees(value: Decimal, unit: str | int) -> Decimal:
    """`value` in document units as rupees, rounded half up to the paisa (DECIMAL(20,2)). `unit`
    is a unit name ("lakh") or rupees per unit (100000). The one place figures become rupees."""
    per_unit = RUPEES_PER_UNIT[unit] if isinstance(unit, str) else Decimal(unit)
    return (value * per_unit).quantize(_PAISE, rounding=ROUND_HALF_UP)


def is_nil(text: str) -> bool:
    """True for a figure printed as nil: '-', '–', '—' or 'nil'."""
    return text.strip().lower() in NIL


def parse_figure(text: str) -> Decimal | None:
    """A figure as printed -> Decimal in document units: '1,234.56' -> 1234.56, '(56.67)' and
    '-56.67' -> -56.67, '.82' -> 0.82, a dash or 'nil' -> 0. None when `text` is not a figure."""
    text = text.strip()
    if is_nil(text):
        return Decimal(0)
    match = _FIGURE.match(text)
    if match is None:
        return None
    opening, minus, digits, closing = match.groups()
    value = Decimal(digits.replace(",", ""))
    return -value if (opening and closing) or minus else value
