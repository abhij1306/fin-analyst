"""Column profiles for tabular source files (`templates/profiles/*.toml`, docs/diligence.md D2).

A profile says how to read one export layout, so the loader (`fin.extract.ledger`) needs no layout
special cases:

- `kind`: the source file kind it reads (`trial_balance`, `ledger_vouchers`, `payroll`, `bank`).
- `header_rows`: how many rows the column header spans. A column's header text is its cells in those
  rows joined by spaces (a merged cell counts in every column it spans), so "Closing Balance" over
  "Debit" reads "Closing Balance Debit".
- `[columns]`: header text -> field. Matching ignores case and repeated spaces.
- `date_format`: `strptime` format of dates written as text (Excel dates are read as they are).
- `period`: a regular expression with groups `start` and `end` that finds the period covered in
  the export's header lines (the rows above the column header). A payroll snapshot has one date,
  the group `date` ("Employee master as on 31-Mar-2025").
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
- `[mask]`: `groups` (group name -> masked kind: the ledgers under it are tokenised, as
  `fin.diligence.mask`) and `fields` (fields in which known masked names are replaced by tokens).
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
KINDS = ("trial_balance", "ledger_vouchers", "registers", "payroll", "bank")  # D3
FIELDS = {
    "trial_balance": {"name", "debit", "credit"},
    "ledger_vouchers": {"date", "particulars", "voucher_type", "voucher_no", "debit", "credit"},
    "payroll": {"employee_name", "employee_code", "gross_salary", "ctc"},
    "bank": {"date", "remarks", "debit", "credit", "balance"},  # reference is optional
}
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
MASK_KINDS = ("customer", "vendor", "user", "employee_name", "employee_code", "ledger")


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
    mask_groups: dict[str, str] = field(default_factory=dict)  # normalised group -> kind
    mask_fields: tuple[str, ...] = ()
    parts: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # registers: sheet kind -> columns
    bank: str = ""  # bank profiles: the bank's name
    currency: str = "INR"  # bank profiles: the currency when the header gives none
    header: dict[str, re.Pattern[str]] = field(default_factory=dict)  # bank profiles


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
    missing = FIELDS[kind] - set(columns.values())
    if missing:
        raise _invalid(name, f"[columns] has no header for {', '.join(sorted(missing))}")
    rows, mask = data.get("rows") or {}, data.get("mask") or {}
    groups = {norm(k): v for k, v in (mask.get("groups") or {}).items()}
    bad = sorted(set(groups.values()) - set(MASK_KINDS))
    if bad:
        raise _invalid(name, f"[mask] groups kinds {', '.join(bad)} are not one of {MASK_KINDS}")
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
            mask_groups=groups,
            mask_fields=tuple(mask.get("fields", [])),
            bank=str(data.get("bank", "")),
            currency=str(data.get("currency", "INR")),
            header={
                k: re.compile(str(v), re.IGNORECASE) for k, v in (data.get("header") or {}).items()
            },
        )
    except (KeyError, ValueError, TypeError, re.error) as exc:
        raise _invalid(name, f"{type(exc).__name__}: {exc}") from exc
    needs = ("date",) if kind == "payroll" else ("start", "end")
    if not set(needs) <= set(period.groupindex):
        raise _invalid(name, f"period needs the named groups {' and '.join(needs)}")
    if profile.group != "bold" or profile.level not in ("indent", "none"):
        raise _invalid(name, 'rows.group must be "bold" and rows.level "indent" or "none"')
    if kind == "bank":
        _check_bank(name, profile)
    return profile


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
