"""Column profiles for tabular source files (`templates/profiles/*.toml`, DILIGENCE_PLAN.md D2).

A profile says how to read one export layout, so the loader (`fin.extract.ledger`) needs no layout
special cases:

- `kind`: the source file kind it reads (`trial_balance`, `ledger_vouchers`).
- `header_rows`: how many rows the column header spans. A column's header text is its cells in those
  rows joined by spaces (a merged cell counts in every column it spans), so "Closing Balance" over
  "Debit" reads "Closing Balance Debit".
- `[columns]`: header text -> field. Matching ignores case and repeated spaces.
- `date_format`: `strptime` format of dates written as text (Excel dates are read as they are).
- `period`: a regular expression with groups `start` and `end` that finds the period covered in
  the export's header lines (the rows above the column header).
- `unit`: rupees per unit of the amounts (default 1).
- `[rows]`: `skip` (names of rows that are not data: totals, opening and closing balances);
  for a trial balance `group` ("bold": group rows are bold) and `level` ("indent": nesting from
  the name cell's indent, or "none"); for Ledger Vouchers `block` (the field whose cell holds the
  ledger name at the head of each ledger block) and `block_prefix` (text before the name, removed).
- `[mask]`: `groups` (group name -> masked kind: the ledgers under it are tokenised, as
  `fin.diligence.mask`) and `fields` (fields in which known masked names are replaced by tokens).

E807 when a profile is unknown or invalid, or no profile of the kind matches the file's headers.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin.audit.errors import LayoutUnknown

PROFILES_DIR = Path(__file__).resolve().parents[3] / "templates" / "profiles"
KINDS = ("trial_balance", "ledger_vouchers")  # D3 adds registers, bank, payroll
FIELDS = {
    "trial_balance": {"name", "debit", "credit"},
    "ledger_vouchers": {"date", "particulars", "voucher_type", "voucher_no", "debit", "credit"},
}
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
    period: re.Pattern[str]
    unit: int = 1
    skip: frozenset[str] = frozenset()  # normalised row names that are not data
    group: str = "bold"
    level: str = "indent"
    block: str = "date"
    block_prefix: str = ""
    mask_groups: dict[str, str] = field(default_factory=dict)  # normalised group -> kind
    mask_fields: tuple[str, ...] = ()


def _invalid(name: str, why: str) -> LayoutUnknown:
    return LayoutUnknown(
        f"column profile {name!r} ({PROFILES_DIR.name}/{name}.toml) is invalid: {why}"
    )


def _parse(name: str, data: dict[str, Any]) -> Profile:
    kind = data.get("kind")
    if kind not in KINDS:
        raise _invalid(name, f"kind {kind!r} is not one of {', '.join(KINDS)}")
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
        )
    except (KeyError, ValueError, TypeError, re.error) as exc:
        raise _invalid(name, f"{type(exc).__name__}: {exc}") from exc
    if not {"start", "end"} <= set(period.groupindex):
        raise _invalid(name, "period needs the named groups start and end")
    if profile.group != "bold" or profile.level not in ("indent", "none"):
        raise _invalid(name, 'rows.group must be "bold" and rows.level "indent" or "none"')
    return profile


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
