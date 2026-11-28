"""Column profiles for tabular source files (`templates/profiles/*.toml`, docs/diligence.md D2).

A profile says how to read one export layout, so the loader (`fin.extract.ledger`) needs no layout
special cases:

- `kind`: the source file kind it reads (`trial_balance`, `ledger_vouchers`, `payroll`, `bank`,
  `orderbook`).
- `header_rows`: how many rows the column header spans. A column's header text is its cells in those
  rows joined by spaces (a merged cell counts in every column it spans), so "Closing Balance" over
  "Debit" reads "Closing Balance Debit".
- `[columns]`: header text -> field. Matching ignores case and repeated spaces.
- `date_format`: `strptime` format of dates written as text (Excel dates are read as they are).
- `period`: a regular expression with groups `start` and `end` that finds the period covered in
  the export's header lines (the rows above the column header). A payroll snapshot or an orderbook
  has one date, the group `date` ("Employee master as on 31-Mar-2025", "Orderbook as at ...").
- `unit`: rupees per unit of the amounts (default 1).
- `[rows]`: `skip` (names of rows that are not data: totals, opening and closing balances);
  for a trial balance `group` ("bold": group rows are bold) and `level` ("indent": nesting from
  the name cell's indent, or "none"); for Ledger Vouchers `block` (the field whose cell holds the
  ledger name at the head of each ledger block) and `block_prefix` (text before the name, removed).
- `registers` profiles describe a billing-system workbook (a sales and a purchase sheet).
  Instead of `[columns]` they have `[sales.columns]` and `[purchase.columns]` (header text -> field,
  see `REGISTER_FIELDS`; `REGISTER_REQUIRED` fields must be mapped), `header_rows`, `date_format`,
  `unit` and `[rows] skip` (total rows). They have no `period`: a register has no period header,
  its period is read from its invoice dates.
- GST return summaries (`kind = "gst"`, docs/fdd.md F6): one row per return month. `gst_return` is
  `gstr1` or `gstr3b` (what the file is stored as); `month_format` is the `strptime` format of a
  month written as text ("Apr-2024": "%b-%Y"); `period` gives the period covered as for a trial
  balance. A GSTR-3B profile also maps `GST3B_FIELDS`. `[rows] skip`: month cells that are not
  months (totals). A GSTR-2B profile (`gst_return = "gstr2b"`, SPEC-20) reads supplier documents
  instead of months: it maps `GSTR2B_FIELDS` (and may map `GSTR2B_OPTIONAL`), has no
  `month_format`, and its `[columns]` may give two headings for one field (the B2B sheet's
  "Invoice number" and the B2B-CDNR sheet's "Note number").
- Bank statements (`kind = "bank"`, Excel and PDF alike): `bank` (the bank's name), `currency` (the
  account's currency when the header does not say) and `[header]`: regular expressions with a group
  `value` that find `account`, `currency`, `opening` and `closing` in the lines around the table
  (`opening` and `closing` are optional: the first row's and last row's balance stand in). The
  amounts are in the account's own currency: `unit` stays 1.

E807 when a profile is unknown or invalid, or no profile of the kind matches the file's headers.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin.audit.errors import LayoutUnknown

PROFILES_DIR = Path(__file__).resolve().parents[3] / "templates" / "profiles"
# D3; F6 gst, F7 orderbook
KINDS = ("trial_balance", "ledger_vouchers", "registers", "payroll", "bank", "orderbook", "gst")
FIELDS = {
    "trial_balance": {"name", "debit", "credit"},
    "ledger_vouchers": {"date", "particulars", "voucher_type", "voucher_no", "debit", "credit"},
    "payroll": {"employee_name", "employee_code", "gross_salary", "ctc"},
    "bank": {"date", "remarks", "debit", "credit", "balance"},  # reference is optional
    # F7: order_date, currency, start_date and end_date are optional
    "orderbook": {"customer", "order_no", "value", "billed"},
    "gst": {"month", "taxable_value", "igst", "cgst", "sgst", "cess"},
}
GST_RETURNS = ("gstr1", "gstr3b", "gstr2b")
GST3B_FIELDS = {"itc", "rcm_liability", "paid_cash", "paid_credit"}  # and GSTR-3B
# GSTR-2B (SPEC-20): one row per supplier document, not per month; the rest are optional
GSTR2B_FIELDS = {
    "vendor", "invoice_no", "document_type", "invoice_date", "taxable_value", "igst", "cgst",
    "sgst", "cess",
}  # fmt: skip
GSTR2B_OPTIONAL = {"invoice_value", "return_period", "filing_date", "itc_available"}
REGISTER_PARTS = ("sales", "purchase")
REGISTER_FIELDS = {
    "bill_id", "purchase_id", "customer_id", "customer", "vendor_id", "vendor", "location",
    "invoice_no", "contract_start", "contract_end", "invoice_date", "order_type", "product",
    "service_start", "service_end", "quantity", "rate", "amount", "currency", "currency_rate",
    "amount_inr", "service_type", "handler", "submitted_by",
}  # fmt: skip
REGISTER_REQUIRED = {
    "sales": {"invoice_no", "invoice_date", "customer", "amount_inr"},
    "purchase": {"invoice_no", "invoice_date", "vendor", "amount_inr"},
}
HEADER_KEYS = ("account", "currency", "opening", "closing")  # a bank profile's [header]


def norm(text: object) -> str:
    """Header or name text for matching: lower case, spaces collapsed; '' for a blank cell."""
    return " ".join(str(text).split()).casefold() if text is not None else ""


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    columns: dict[str, str]  # normalised header text -> field
    header_rows: int
    date_format: str
    period: re.Pattern[str] | None = None  # not for registers
    unit: int = 1
    skip: frozenset[str] = frozenset()  # normalised row names that are not data
    group: str = "bold"
    level: str = "indent"
    block: str = "date"
    block_prefix: str = ""
    parts: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # registers: sheet kind -> columns
    bank: str = ""  # bank profiles: the bank's name
    currency: str = "INR"  # bank profiles: the currency when the header gives none
    header: dict[str, re.Pattern[str]] = field(default_factory=dict)  # bank profiles
    gst_return: str = ""  # gst profiles: gstr1 or gstr3b
    month_format: str = ""  # gst profiles: a month written as text


def _invalid(name: str, why: str) -> LayoutUnknown:
    return LayoutUnknown(
        f"column profile {name!r} ({PROFILES_DIR.name}/{name}.toml) is invalid: {why}"
    )


def _parse_registers(name: str, data: dict[str, Any]) -> Profile:
    parts = {}
    for part in REGISTER_PARTS:
        columns = {norm(k): v for k, v in ((data.get(part) or {}).get("columns") or {}).items()}
        unknown = sorted(set(columns.values()) - REGISTER_FIELDS)
        if unknown:
            raise _invalid(name, f"[{part}.columns] fields {', '.join(unknown)} are not known")
        missing = REGISTER_REQUIRED[part] - set(columns.values())
        if missing:
            raise _invalid(name, f"[{part}.columns] has no header for {', '.join(sorted(missing))}")
        parts[part] = columns
    try:
        return Profile(
            name=name,
            kind="registers",
            columns={},
            header_rows=int(data.get("header_rows", 1)),
            date_format=str(data["date_format"]),
            unit=int(data.get("unit", 1)),
            skip=frozenset(norm(s) for s in (data.get("rows") or {}).get("skip", [])),
            parts=parts,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise _invalid(name, f"{type(exc).__name__}: {exc}") from exc


def _parse(name: str, data: dict[str, Any]) -> Profile:
    kind = data.get("kind")
    if kind not in KINDS:
        raise _invalid(name, f"kind {kind!r} is not one of {', '.join(KINDS)}")
    if kind == "registers":
        return _parse_registers(name, data)
    columns = {norm(k): v for k, v in (data.get("columns") or {}).items()}
    is_2b = kind == "gst" and data.get("gst_return") == "gstr2b"
    missing = (GSTR2B_FIELDS if is_2b else FIELDS[kind]) - set(columns.values())
    if missing:
        raise _invalid(name, f"[columns] has no header for {', '.join(sorted(missing))}")
    rows = data.get("rows") or {}
    try:
        period = re.compile(str(data["period"]), re.IGNORECASE)
        profile = Profile(
            name=name,
            kind=kind,
            columns=columns,
            header_rows=int(data.get("header_rows", 1)),
            date_format=str(data["date_format"]),
            period=period,
            unit=int(data.get("unit", 1)),
            skip=frozenset(norm(s) for s in rows.get("skip", [])),
            group=str(rows.get("group", "bold")),
            level=str(rows.get("level", "indent")),
            block=str(rows.get("block", "date")),
            block_prefix=str(rows.get("block_prefix", "")),
            bank=str(data.get("bank", "")),
            currency=str(data.get("currency", "INR")),
            header={
                k: re.compile(str(v), re.IGNORECASE) for k, v in (data.get("header") or {}).items()
            },
            gst_return=str(data.get("gst_return", "")),
            month_format=str(data.get("month_format", "")),
        )
    except (KeyError, ValueError, TypeError, re.error) as exc:
        raise _invalid(name, f"{type(exc).__name__}: {exc}") from exc
    needs = ("date",) if kind in ("payroll", "orderbook") else ("start", "end")
    if not set(needs) <= set(period.groupindex):
        raise _invalid(name, f"period needs the named groups {' and '.join(needs)}")
    if profile.group != "bold" or profile.level not in ("indent", "none"):
        raise _invalid(name, 'rows.group must be "bold" and rows.level "indent" or "none"')
    _check_kind(name, profile)
    return profile


def _check_kind(name: str, profile: Profile) -> None:
    """The checks only bank and gst profiles have."""
    if profile.kind == "bank":
        _check_bank(name, profile)
    if profile.kind == "gst":
        _check_gst(name, profile)


def _check_gst(name: str, profile: Profile) -> None:
    if profile.gst_return == "gstr2b":
        unknown = set(profile.columns.values()) - GSTR2B_FIELDS - GSTR2B_OPTIONAL
        if unknown:
            raise _invalid(name, f"[columns] fields {', '.join(sorted(unknown))} are not known")
        return
    if profile.gst_return not in GST_RETURNS or not profile.month_format:
        raise _invalid(
            name, 'a gst profile needs gst_return = "gstr1", "gstr3b" or "gstr2b" and month_format'
        )
    missing = GST3B_FIELDS - set(profile.columns.values()) if profile.gst_return == "gstr3b" else ()
    if missing:
        raise _invalid(name, f"[columns] has no header for {', '.join(sorted(missing))}")


def _check_bank(name: str, profile: Profile) -> None:
    if not profile.bank or profile.unit != 1:
        raise _invalid(name, 'a bank profile needs bank = "<name>" and unit 1')
    bad = sorted(set(profile.header) - set(HEADER_KEYS))
    if bad or "account" not in profile.header:
        raise _invalid(name, f"[header] needs account and may hold {HEADER_KEYS}; got {bad}")
    for key, pattern in profile.header.items():
        if "value" not in pattern.groupindex:
            raise _invalid(name, f"[header] {key} needs a group named value")


def load_profile(name: str) -> Profile:
    """The profile `templates/profiles/<name>.toml`. E807 when there is none or it is invalid."""
    path = PROFILES_DIR / f"{name}.toml"
    if not re.fullmatch(r"[a-z0-9_]+", name) or not path.is_file():
        known = ", ".join(p.stem for p in sorted(PROFILES_DIR.glob("*.toml")))
        raise LayoutUnknown(f"no column profile {name!r}; known profiles: {known}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _invalid(name, str(exc)) from exc
    return _parse(name, data)


def profiles_for(kind: str) -> list[Profile]:
    """Every profile of `kind`, by name."""
    found = [load_profile(p.stem) for p in sorted(PROFILES_DIR.glob("*.toml"))]
    return [p for p in found if p.kind == kind]
