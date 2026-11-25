"""The terms of a contract reading (docs/fdd.md F1), for `fin.reconcile.contracts`.

Beside the columns of the recast's reading (customer, start, end, value, billing_terms, page,
invoice_no) a row may carry, all optional:

- `party_kind` (customer or vendor; default customer) and `counterparty` (the token or name;
  `customer` is still read as the counterparty column);
- `lock_in_months`, `notice_days`: whole numbers; `escalation_pct`: the escalation a year, as
  `5` or `5%`;
- `sla_terms`, `penalty_terms`, `escalation_terms`, `renewal_terms`: as read, free text (names
  are masked on load);
- `auto_renew`, `termination_convenience`, `change_of_control`, `exclusivity`: yes or no.

An empty cell means the reading did not say (stored as NULL, shown as "not read"); it is not "no
clause". fin does not check a Claude reading: trusting it is the user's decision.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

PARTY_KINDS = ("customer", "vendor")
TEXT_TERMS = ("billing_terms", "sla_terms", "penalty_terms", "escalation_terms", "renewal_terms")
INT_TERMS = ("lock_in_months", "notice_days")
FLAG_TERMS = ("auto_renew", "termination_convenience", "change_of_control", "exclusivity")
OPTIONAL = (
    "party_kind",
    "invoice_no",
    "value",
    *TEXT_TERMS,
    *INT_TERMS,
    "escalation_pct",
    *FLAG_TERMS,
)
TERM_COLUMNS = (*TEXT_TERMS, *INT_TERMS, "escalation_pct", *FLAG_TERMS)
_YES, _NO = ("yes", "y", "true", "1"), ("no", "n", "false", "0")


def _flag(name: str, text: str) -> bool:
    lowered = text.lower()
    if lowered in _YES:
        return True
    if lowered in _NO:
        return False
    raise ValueError(f"{name} {text!r} is not yes or no")


def _whole(name: str, text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        raise ValueError(f"{name} {text!r} is not a whole number") from None
    if number < 0:
        raise ValueError(f"{name} is below 0")
    return number


def _percent(text: str) -> Decimal:
    try:
        number = Decimal(text.rstrip("%").strip())
    except InvalidOperation:
        raise ValueError(f"escalation_pct {text!r} is not a number") from None
    if not number.is_finite() or number < 0:
        raise ValueError("escalation_pct is below 0")
    return number


def parse_terms(row: dict[str, str]) -> dict[str, Any]:
    """party_kind and every term column of a reading row (empty -> None). ValueError names what
    is wrong."""
    kind = row.get("party_kind", "").lower() or "customer"
    if kind not in PARTY_KINDS:
        raise ValueError(f"party_kind {kind!r} is not one of {', '.join(PARTY_KINDS)}")
    terms: dict[str, Any] = {"party_kind": kind}
    for name in TERM_COLUMNS:
        text = row.get(name, "")
        if not text:
            terms[name] = None
        elif name in INT_TERMS:
            terms[name] = _whole(name, text)
        elif name in FLAG_TERMS:
            terms[name] = _flag(name, text)
        elif name == "escalation_pct":
            terms[name] = _percent(text)
        else:
            terms[name] = text
    return terms


def terms_text(row: dict[str, Any]) -> str:
    """The terms a contract has, as ', lock-in 12 months, change of control: yes' (not read ones
    are left out)."""
    parts = []
    for name in INT_TERMS:
        if row.get(name) is not None:
            parts.append(f"{name.replace('_', ' ').replace(' months', '')} {row[name]}")
    if row.get("escalation_pct") is not None:
        parts.append(f"escalation {row['escalation_pct']}% a year")
    for name in FLAG_TERMS:
        if row.get(name) is not None:
            parts.append(f"{name.replace('_', ' ')}: {'yes' if row[name] else 'no'}")
    for name in TEXT_TERMS[1:]:
        if row.get(name):
            parts.append(f"{name.replace('_terms', '')} {row[name]}")
    return "".join(f", {part}" for part in parts)
