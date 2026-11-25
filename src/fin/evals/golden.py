"""The golden set: `evals/<company>/golden.csv` in the data folder, never committed (docs/EVAL.md).

One line per check, the value a person read in the company's document and checked:

- `statement`: a statement figure (account, period, scope) as `current_facts` gives it;
- `tb_ledger`: a trial balance ledger's closing balance, debit less credit (ledger as stored: the
  token for a party);
- `tb_vs_statements`: the difference on an account in the period's latest `tb_vs_statements` run
  (trial balance less statements);
- `stub`: an account's figure in `stub_figures` (unaudited management accounts);
- `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`: the difference on
  one detail line of the period's latest run of that reconciliation, register (or Ledger Vouchers,
  or payroll) less books (or trial balance); `ledger` is the invoice number, the ledger as
  stored, or the period for payroll.
- `bank_closing`: a bank statement's closing balance, in the account's own currency (use unit
  `rupees` for the amount as printed, INR or USD): period is the statement's end date (YYYY-MM-DD),
  ledger its account number;
- `core_revenue`, `passthrough_revenue`: the period's sales register revenue (Amount in Rs, before
  GST) of customers not on, and on, the engagement's pass-through list (`revenue_split`);
- `adjusted_revenue`, `adjusted_ebitda`: the period's figure after accepted adjustments
  (`adjusted_ebitda` view), by scope.
- `register_total`: the sum of Amount in Rs (before GST) over every row of the sales or the purchase
  register in the loaded registers file: period is the file's period (as `fin status` shows it, e.g.
  `FY25` or `FY24-FY25`), ledger `sales` or `purchase`;
- `payroll_total`: the sum of gross salary or of CTC (annual rupees) over the employees of the
  loaded payroll file: period is the file's period, ledger `gross_salary` or `ctc`.

`expected` is the figure as printed (brackets or a minus for negative) in `unit`; `source` is where
it is printed (file + sheet!cell, sheet!row or page). Only a line whose `checked` is `yes` is
scored: Claude does not verify its own reading, so a person sets it. `fin eval init` lists what to
check with its source and leaves `expected` and `checked` empty.
"""

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fin.audit.errors import GoldenSetInvalid, GoldenSetMissing
from fin.config import SCOPES
from fin.extract.units import RUPEES_PER_UNIT, parse_figure, to_rupees
from fin.map.dictionary import Account
from fin.paths import evals_dir

COLUMNS = ("kind", "period", "scope", "account", "ledger", "expected", "unit", "source", "checked")
OPTIONAL = ("note",)
RECON_KINDS = ("sales_register_vs_gl", "purchase_register_vs_gl", "gl_vs_tb", "payroll_vs_gl")
PASSTHROUGH_KINDS = ("core_revenue", "passthrough_revenue", "adjusted_revenue", "adjusted_ebitda")
KINDS = (
    "statement",
    "tb_ledger",
    "tb_vs_statements",
    "stub",
    "bank_closing",
    *RECON_KINDS,
    *PASSTHROUGH_KINDS,
    "register_total",
    "payroll_total",
)
NEEDS = {
    "statement": ("period", "scope", "account"),
    "tb_ledger": ("period", "ledger"),
    "tb_vs_statements": ("period", "account"),
    "stub": ("period", "account"),
    "bank_closing": ("period", "ledger"),
    **{kind: ("period", "ledger") for kind in RECON_KINDS},
    "core_revenue": ("period",),
    "passthrough_revenue": ("period",),
    "adjusted_revenue": ("period", "scope"),
    "adjusted_ebitda": ("period", "scope"),
    "register_total": ("period", "ledger"),
    "payroll_total": ("period", "ledger"),
}
LEDGERS = {"register_total": ("sales", "purchase"), "payroll_total": ("gross_salary", "ctc")}
YES, NO = "yes", ("", "no")
# Diligence checks read the latest engagement's files and runs: a closed engagement's trial balance
# for the same period is not added to the current one.
LATEST_ENGAGEMENT = "(SELECT max(engagement_id) FROM engagement)"


@dataclass(frozen=True)
class Check:
    line: int  # the line of golden.csv (the header is line 1)
    kind: str
    period: str
    scope: str
    account: str
    ledger: str
    expected: str
    unit: str
    source: str
    checked: bool
    note: str = ""

    @property
    def key(self) -> tuple[str, ...]:
        return (self.kind, self.period, self.scope, self.account, self.ledger)

    @property
    def rupees(self) -> Decimal | None:
        """The expected value in rupees (None when empty)."""
        if not self.expected.strip() or not self.unit.strip():
            return None
        figure = parse_figure(self.expected)
        return None if figure is None else to_rupees(figure, unit_name(self.unit))


def golden_path(root: Path, company: str) -> Path:
    return evals_dir(root) / company / "golden.csv"


def unit_name(text: str) -> str:
    """'Lakhs' -> 'lakh'; KeyError for a unit fin does not know."""
    word = text.strip().lower()
    word = word if word in RUPEES_PER_UNIT else word.removesuffix("s")
    if word not in RUPEES_PER_UNIT:
        raise KeyError(text)
    return word


def _unit_problems(check: Check, accounts: Mapping[str, Account]) -> list[str]:
    try:
        unit = unit_name(check.unit)
    except KeyError:
        return [f"unit {check.unit!r} is not one of {', '.join(RUPEES_PER_UNIT)}"]
    account = accounts.get(check.account)
    if account is not None and account.unit != "rupees" and unit != "rupees":
        return [f"{check.account} is stored as printed ({account.unit}): use unit rupees"]
    return []


def _problems(check: Check, accounts: Mapping[str, Account]) -> list[str]:
    out = [f"{name} is empty" for name in NEEDS[check.kind] if not getattr(check, name)]
    if check.ledger and check.kind in LEDGERS and check.ledger not in LEDGERS[check.kind]:
        out.append(f"ledger {check.ledger!r} is not one of {', '.join(LEDGERS[check.kind])}")
    if check.scope and check.scope not in SCOPES:
        out.append(f"scope {check.scope!r} is not one of {', '.join(SCOPES)}")
    if check.account and check.account not in accounts:
        out.append(f"account {check.account!r} is not in the dictionary (`fin map accounts`)")
    if check.expected.strip() and parse_figure(check.expected) is None:
        out.append(f"expected {check.expected!r} is not a figure")
    if check.unit.strip():
        out += _unit_problems(check, accounts)
    if check.checked:
        out += [
            f"checked, but {n} is empty"
            for n in ("expected", "unit", "source")
            if not getattr(check, n).strip()
        ]
    return out


def read_golden(root: Path, company: str, accounts: Mapping[str, Account]) -> list[Check]:
    """Every line of the company's golden set. E830 when there is none; E831 listing every line at
    fault when any is."""
    path = golden_path(root, company)
    if not path.is_file():
        raise GoldenSetMissing(
            f"{company} has no golden set ({path}); make one with `fin eval init {company}`"
        )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise GoldenSetInvalid(f"{path}: missing columns {', '.join(missing)}; nothing scored")
        rows = list(reader)
    checks, problems, seen = [], [], set()
    for number, row in enumerate(rows, start=2):
        values = {c: (row.get(c) or "").strip() for c in (*COLUMNS, *OPTIONAL)}
        if values["kind"] not in KINDS:
            problems.append(
                f"line {number}: kind {values['kind']!r} is not one of {', '.join(KINDS)}"
            )
            continue
        if values["checked"].lower() not in (YES, *NO):
            problems.append(
                f"line {number}: checked is {values['checked']!r}; use yes or leave it empty"
            )
            continue
        checked = values.pop("checked").lower() == YES
        check = Check(number, checked=checked, **values)
        problems += [f"line {number}: {p}" for p in _problems(check, accounts)]
        if check.key in seen:
            problems.append(f"line {number}: the same check as an earlier line")
        seen.add(check.key)
        checks.append(check)
    if problems:
        raise GoldenSetInvalid(f"{path}: " + "; ".join(problems) + "; nothing scored")
    return checks


def write_golden(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*COLUMNS, *OPTIONAL])
        writer.writeheader()
        writer.writerows(rows)
