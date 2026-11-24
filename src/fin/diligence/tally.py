"""What the rows of a Tally Trial Balance or Ledger Vouchers export mean (DILIGENCE_PLAN.md D2).

The rows come from `fin.extract.ledger.read_table`, read with a column profile; everything
layout-specific is in the profile.

- Trial balance: a bold row is a group (its closing balance is the group total), any other row a
  ledger. Nesting comes from the name cell's indent (profile `level = "indent"`): a row sits in the
  nearest group above it with a smaller indent. A ledger under a group the profile's `[mask]
  groups` names (Sundry Debtors, Sundry Creditors) is a party: its name is stored as a token. A TB
  with no ledger rows is refused (E809: export it in Detailed mode). A group whose net balance
  (debit - credit) is not the net of the ledgers under it is a finding; the rows stay as filed.
- Ledger Vouchers: a row with no date whose block field holds text and which has no amounts starts a
  ledger block (the ledger name, after `block_prefix`); each dated row below is a voucher line of
  that ledger. Rows without a date (opening and closing balance, totals) are not loaded. A ledger
  that is a known masked name gets its token; one not in the period's trial balance cannot be
  classified, so it is masked too (`LEDG-0001`). Known names in the masked fields are replaced.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fin.audit.errors import GroupsOnlyTrialBalance, ParseError
from fin.diligence.mask import Masker
from fin.extract.ledger import SheetRow, Table, amount, parse_date
from fin.extract.profiles import norm


@dataclass(frozen=True)
class TbRow:
    sheet: str
    row: int
    row_type: str  # 'group' or 'ledger'
    name: str  # as filed
    group: int | None  # index in the list of the group it sits in
    debit: Decimal
    credit: Decimal

    @property
    def net(self) -> Decimal:
        return self.debit - self.credit


def _text(value: Any) -> str:
    """A cell as text: an integral number without '.0', spaces collapsed; '' for blank."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def trial_balance_rows(table: Table, file_name: str) -> list[TbRow]:
    """The group and ledger rows, in order. E809 when there is no ledger row; E204 on a bad
    amount."""
    profile = table.profile
    rows: list[TbRow] = []
    stack: list[tuple[float, int]] = []  # (level, index in rows) of the open groups
    for r in table.rows:
        name = _text(r.cells.get("name"))
        if not name or norm(name) in profile.skip:
            continue
        where = f"{file_name} {r.sheet}!{r.row}"
        debit = amount(r.cells.get("debit"), profile, where)
        credit = amount(r.cells.get("credit"), profile, where)
        level = r.indent if profile.level == "indent" else (0.0 if r.bold else 1.0)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        rows.append(
            TbRow(r.sheet, r.row, "group" if r.bold else "ledger", name, parent, debit, credit)
        )
        if r.bold:
            stack.append((level, len(rows) - 1))
    if not any(row.row_type == "ledger" for row in rows):
        raise GroupsOnlyTrialBalance(
            f"{file_name}: the trial balance has group rows only; export it again from Tally in "
            "Detailed mode (with ledgers) and ingest that file. Nothing loaded"
        )
    return rows


def ancestors(rows: list[TbRow], index: int) -> list[int]:
    """The groups a row sits in, nearest first."""
    out, group = [], rows[index].group
    while group is not None:
        out.append(group)
        group = rows[group].group
    return out


def party_kind(rows: list[TbRow], index: int, groups: dict[str, str]) -> str | None:
    """The masked kind of a ledger under a party group (the nearest one named), else None."""
    return next(
        (
            groups[norm(rows[g].name)]
            for g in ancestors(rows, index)
            if norm(rows[g].name) in groups
        ),
        None,
    )


def stored_ledger(rows: list[TbRow], index: int, table: Table, masker: Masker) -> str:
    """A ledger's name as stored: a token for a party ledger, known names masked otherwise."""
    kind = party_kind(rows, index, table.profile.mask_groups)
    name = rows[index].name
    if kind is not None:
        return masker.token(kind, name)
    if "name" in table.profile.mask_fields:
        return masker.text(name) or name
    return name


def group_sum_findings(rows: list[TbRow], file_name: str, period: str) -> list[dict[str, Any]]:
    """A finding (columns of the `findings` table) for each group whose net balance is not the net
    of the ledgers under it."""
    sums: dict[int, Decimal] = {
        i: Decimal("0.00") for i, r in enumerate(rows) if r.row_type == "group"
    }
    counts = dict.fromkeys(sums, 0)
    for i, r in enumerate(rows):
        if r.row_type == "ledger":
            for g in ancestors(rows, i):
                sums[g] += r.net
                counts[g] += 1
    out = []
    for g, total in sums.items():
        group = rows[g]
        if total == group.net:
            continue
        out.append(
            {
                "file_name": file_name,
                "check_name": "tb_group_sum",
                "finding": "group_not_sum_of_ledgers",
                "statement": "trial_balance",
                "period": period,
                "label": group.name,
                "fin_value": group.net,
                "other_value": total,
                "difference": total - group.net,
                "fin_source": f"{group.sheet}!{group.row}",
                "other_source": f"sum of the {counts[g]} ledgers under it",
                "message": (
                    f"trial balance {period}: group {group.name!r} net closing balance (debit - "
                    f"credit) is not the net of the {counts[g]} ledgers under it; rows kept as "
                    "filed"
                ),
            }
        )
    return out


@dataclass(frozen=True)
class Entry:
    sheet: str
    row: int
    ledger: str  # as filed
    date: Any
    particulars: str
    voucher_type: str
    voucher_no: str
    debit: Decimal
    credit: Decimal


def _block_name(r: SheetRow, table: Table) -> str | None:
    """The ledger name a block-header row gives, else None."""
    profile = table.profile
    text = _text(r.cells.get(profile.block))
    if not text or norm(text) in profile.skip or r.cells.get("debit") or r.cells.get("credit"):
        return None
    prefix = profile.block_prefix
    if prefix and text.casefold().startswith(prefix.casefold()):
        text = text[len(prefix) :].strip()
    return text or None


def ledger_entries(table: Table, file_name: str) -> list[Entry]:
    """The voucher lines, each with the ledger of the block it is in. E204 on a bad amount or a
    voucher line before any ledger name."""
    profile = table.profile
    ledger: str | None = None
    out = []
    for r in table.rows:
        when = parse_date(r.cells.get("date"), profile)
        if when is None:
            ledger = _block_name(r, table) or ledger
            continue
        where = f"{file_name} {r.sheet}!{r.row}"
        if ledger is None:
            raise ParseError(f"{where}: a voucher line before any ledger name")
        out.append(
            Entry(
                r.sheet,
                r.row,
                ledger,
                when,
                _text(r.cells.get("particulars")),
                _text(r.cells.get("voucher_type")),
                _text(r.cells.get("voucher_no")),
                amount(r.cells.get("debit"), profile, where),
                amount(r.cells.get("credit"), profile, where),
            )
        )
    return out
