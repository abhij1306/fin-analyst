"""What the rows of a Tally Trial Balance or Ledger Vouchers export mean (docs/diligence.md D2).

The rows come from `fin.extract.ledger.read_table`, read with a column profile; everything
layout-specific is in the profile.

- Trial balance: a bold row is a group (its closing balance is the group total), any other row a
  ledger. Nesting comes from the name cell's indent (profile `level = "indent"`): a row sits in the
  nearest group above it with a smaller indent. A ledger (or sub-group) under a group the
  profile's `[mask] groups` names (Sundry Debtors, Sundry Creditors) is a party: its name is
  stored as a token. A TB with no ledger rows is refused (E809: export it in Detailed mode). A
  group whose net balance (debit - credit) is not the net of the ledgers under it is a finding;
  the rows stay as filed.
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
from fin.extract.profiles import Profile, norm


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


def _level(r: SheetRow, profile: Profile) -> float:
    """A row's nesting: its name cell's indent, or (level "none") groups above ledgers."""
    if profile.level == "indent":
        return r.indent
    return 0.0 if r.bold else 1.0


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
        level = _level(r, profile)
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


def stored_names(rows: list[TbRow], profile: Profile, masker: Masker) -> list[str]:
    """Each row's name as stored: a token for a row under a party group (a party ledger, or a
    sub-group of parties), with known masked names replaced otherwise (when the profile masks
    `name`). Parties are tokenised first, so a ledger above them that names one (an advance to a
    customer) is masked too."""
    kinds = [party_kind(rows, i, profile.mask_groups) for i in range(len(rows))]
    tokens = [masker.token(k, r.name) if k else None for r, k in zip(rows, kinds, strict=True)]
    if "name" in profile.mask_fields:
        return [t or masker.text(r.name) or r.name for r, t in zip(rows, tokens, strict=True)]
    return [t or r.name for r, t in zip(rows, tokens, strict=True)]


def group_sum_findings(
    rows: list[TbRow], names: list[str], file_name: str, period: str
) -> list[dict[str, Any]]:
    """A finding (columns of the `findings` table) for each group whose net balance is not the net
    of the ledgers under it. `names`: the rows' names as stored (`stored_names`)."""
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
                "label": names[g],
                "fin_value": group.net,
                "other_value": total,
                "difference": total - group.net,
                "fin_source": f"{group.sheet}!{group.row}",
                "other_source": f"sum of the {counts[g]} ledgers under it",
                "message": (
                    f"trial balance {period}: group {names[g]!r} net closing balance (debit - "
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


def _undated_line(r: SheetRow, profile: Profile) -> bool:
    """An undated row with an amount that is not a row the profile skips (by any of its cells)."""
    has_amount = any(_text(r.cells.get(field)) for field in ("debit", "credit"))
    skipped = any(norm(_text(v)) in profile.skip for v in r.cells.values())
    return has_amount and not skipped


@dataclass(frozen=True)
class Block:
    sheet: str
    row: int
    ledger: str  # as filed


def ledger_entries(table: Table, file_name: str) -> tuple[list[Entry], list[Block]]:
    """The voucher lines, each with the ledger of the block it is in, and every ledger block the
    export has, with or without voucher lines (a ledger with only an opening balance). E204 on a
    bad amount, a voucher line before any ledger name, a row with amounts but no date that is not
    a `skip` row (a date in another format would otherwise drop the line), or no voucher line."""
    profile = table.profile
    ledger: str | None = None
    out = []
    blocks: list[Block] = []
    for r in table.rows:
        when = parse_date(r.cells.get("date"), profile)
        where = f"{file_name} {r.sheet}!{r.row}"
        if when is None:
            if _undated_line(r, profile):
                raise ParseError(
                    f"{where}: a row with amounts but no date in the profile's date format "
                    f"({profile.date_format}, profile {profile.name}). If it is not a voucher "
                    "line, add its name to [rows] skip in the profile"
                )
            name = _block_name(r, table)
            if name:
                blocks.append(Block(r.sheet, r.row, name))
            ledger = name or ledger
            continue
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
    if not out:
        raise ParseError(f"{file_name}: no voucher line (a dated row) below the column header")
    return out, blocks
