"""Claude's reading of notes to accounts, legal and tax notices (`fin disclosures load`).

A UTF-8 CSV with a header row, one row per item, columns in any order:

- `kind` (required): one of KINDS;
- `period` (required): the engagement period the item belongs to (FY25, or the stub);
- `nature` (required): what it is, as read;
- `page` (required): the page of the document it was read from, from 1;
- `counterparty`: the party's name as printed, or a token (CUST-0001); required for the related
  party kinds; a register customer or vendor becomes its token on load;
- `amount`: as printed (`12,00,000.00`, `(5.2)`), in `unit`; required for related party
  transactions and balances;
- `unit`: rupees (default), thousand, lakh, crore or million: the unit the document prints;
- `forum`, `stage` (litigation, tax matters); `assessment_year`, `demand`, `paid_under_protest`
  (tax matters; amounts in `unit`); `relationship` (required for related_party);
- `books` (related_party_transaction only): sales, purchases, debit or credit: what rpt_vs_books
  compares the amount with; `ledger` (with debit or credit): the ledger, as printed or as stored.

E940 for a reading that cannot be used, naming each CSV line at fault.
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fin.audit.errors import DisclosureReadingInvalid
from fin.extract.units import RUPEES_PER_UNIT, parse_figure, to_rupees

KINDS = (
    "contingent_liability",
    "commitment",
    "guarantee",
    "litigation",
    "tax_matter",
    "off_balance_sheet",
    "related_party",
    "related_party_transaction",
    "related_party_balance",
)
RELATED = ("related_party", "related_party_transaction", "related_party_balance")
BOOKS = ("sales", "purchases", "debit", "credit")
REQUIRED = ("kind", "period", "nature", "page")
TEXT = ("counterparty", "forum", "stage", "assessment_year", "relationship", "books", "ledger")
MONEY = ("amount", "demand", "paid_under_protest")
OPTIONAL = (*TEXT, *MONEY, "unit")


def _money(text: str, unit: str) -> Decimal | None:
    if not text:
        return None
    figure = parse_figure(text)
    if figure is None:
        raise ValueError(f"{text!r} is not a figure")
    return to_rupees(Decimal(figure), unit)


def _unit(text: str) -> str:
    word = (text or "rupees").lower()
    word = word if word in RUPEES_PER_UNIT else word.removesuffix("s")
    if word not in RUPEES_PER_UNIT:
        raise ValueError(f"unit {text!r} is not one of {', '.join(RUPEES_PER_UNIT)}")
    return word


def _check_kind(row: dict[str, Any]) -> None:
    kind = row["kind"]
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    if kind in RELATED and not row["counterparty"]:
        raise ValueError(f"a {kind} row needs a counterparty")
    if kind in RELATED[1:] and row["amount"] is None:
        raise ValueError(f"a {kind} row needs an amount")
    if kind == "related_party" and not row["relationship"]:
        raise ValueError("a related_party row needs a relationship")
    books = row["books"]
    if books and kind != "related_party_transaction":
        raise ValueError("books applies to related_party_transaction rows only")
    if books and books not in BOOKS:
        raise ValueError(f"books {books!r} is not one of {', '.join(BOOKS)}")
    if (books in ("debit", "credit")) != bool(row["ledger"]):
        raise ValueError("ledger goes with books debit or credit, and they need one")


def parse_row(number: int, raw: dict[str, str], periods: list[str]) -> dict[str, Any]:
    """One validated reading row (amounts in rupees); ValueError names what is wrong."""
    unit = _unit(raw.get("unit", ""))
    row: dict[str, Any] = {name: raw.get(name) or None for name in TEXT}
    row.update(line=number, kind=raw["kind"], period=raw["period"], nature=raw["nature"])
    row.update({name: _money(raw.get(name, ""), unit) for name in MONEY})
    if not row["nature"]:
        raise ValueError("nature is empty")
    if row["period"] not in periods:
        raise ValueError(f"period {row['period']!r} is not one of {', '.join(periods)}")
    row["page"] = int(raw["page"])
    if row["page"] < 1:
        raise ValueError("page starts at 1")
    _check_kind(row)
    return row


def read_rows(path: Path, periods: list[str]) -> list[dict[str, Any]]:
    """The reading's rows, validated against the engagement's periods. E940 listing every line at
    fault (nothing recorded)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = set(reader.fieldnames or [])
        table = list(reader)
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        raise DisclosureReadingInvalid(
            f"{path.name}: missing column(s) {', '.join(missing)} (needs {', '.join(REQUIRED)}; "
            f"optional {', '.join(OPTIONAL)}); nothing recorded"
        )
    if not table:
        raise DisclosureReadingInvalid(f"{path.name}: no rows; nothing recorded")
    rows, problems = [], []
    for number, raw in enumerate(table, start=2):
        clean = {k: (v or "").strip() for k, v in raw.items() if k}
        try:
            rows.append(parse_row(number, clean, periods))
        except (ValueError, InvalidOperation) as exc:
            problems.append(f"line {number}: {exc}")
    if problems:
        raise DisclosureReadingInvalid(f"{path.name}: {'; '.join(problems)}; nothing recorded")
    return rows
