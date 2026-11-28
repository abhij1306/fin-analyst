"""Bank receipts against sales invoices: the fixed matching passes (docs/diligence.md D5).

No fuzzy or ML matching. Receipts (bank credits) and invoices are matched in this order, and each
group records the pass that made it:

  confirmed          a match Claude proposed and the user confirmed (applied first);
  reference          the invoice number appears in the receipt's reference or remarks (whole
                     tokens): a receipt naming several invoices, or several receipts naming one,
                     form one group; the amounts need not agree (the difference is a line);
  party_amount_date  same customer, same amount, dates at most the window apart; the closest
                     dates go first, then the earlier invoice;
  amount_date        same amount, dates within the window, and neither the receipt nor the
                     invoice has another candidate (customers that are both known must agree).

Amounts are compared in the receipt's currency: an INR receipt with an invoice's Amount in Rs, a
foreign-currency receipt with the invoice's own-currency Amount. A receipt and an invoice in
different currencies never match by pass. What is left over is unmatched: it goes to Claude.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

Key = tuple[int, str, int]  # file_id, sheet, row
PASSES = ("confirmed", "reference", "party_amount_date", "amount_date")


@dataclass(frozen=True)
class Receipt:
    key: Key
    day: date
    currency: str
    amount: Decimal
    party: str | None
    text: str  # reference and remarks


@dataclass(frozen=True)
class Invoice:
    key: Key
    day: date
    currency: str
    amount: Decimal  # in `currency`: Amount in Rs for INR, else the invoice's own Amount
    amount_inr: Decimal
    party: str | None
    number: str


@dataclass(frozen=True)
class Group:
    pass_name: str
    receipts: tuple[Receipt, ...]
    invoices: tuple[Invoice, ...]


def _names(text: str, number: str) -> bool:
    pattern = rf"(?<![A-Z0-9]){re.escape(number.upper())}(?![A-Z0-9])"
    return re.search(pattern, text.upper()) is not None


def _find(parent: dict[Key, Key], k: Key) -> Key:
    while parent[k] != k:
        parent[k] = parent[parent[k]]
        k = parent[k]
    return k


def _clusters(receipts: list[Receipt], unique: list[Invoice]) -> dict[Key, list[Key]]:
    """Receipts and invoices joined when the receipt names the invoice, as {root: [keys]}."""
    parent: dict[Key, Key] = {}
    used: set[Key] = set()
    for r in receipts:
        for inv in unique:
            if not _names(r.text, inv.number):
                continue
            for k in (r.key, inv.key):
                parent.setdefault(k, k)
            parent[_find(parent, r.key)] = _find(parent, inv.key)
            used |= {r.key, inv.key}
    clusters: dict[Key, list[Key]] = {}
    for k in sorted(used):
        clusters.setdefault(_find(parent, k), []).append(k)
    return clusters


def _reference_groups(receipts: list[Receipt], invoices: list[Invoice]) -> list[Group]:
    count: dict[str, int] = {}
    for inv in invoices:
        count[inv.number.upper()] = count.get(inv.number.upper(), 0) + 1
    unique = [inv for inv in invoices if count[inv.number.upper()] == 1]
    r_by = {r.key: r for r in receipts}
    i_by = {i.key: i for i in unique}
    groups = []
    for keys in _clusters(receipts, unique).values():
        rs = tuple(r_by[k] for k in keys if k in r_by)
        iv = tuple(i_by[k] for k in keys if k in i_by)
        if len({r.currency for r in rs} | {i.currency for i in iv}) == 1:
            groups.append(Group("reference", rs, iv))
    return groups


def _pairable(r: Receipt, inv: Invoice, window: int, need_party: bool) -> bool:
    if inv.currency != r.currency or inv.amount != r.amount:
        return False
    if abs((r.day - inv.day).days) > window:
        return False
    if need_party:
        return r.party is not None and r.party == inv.party
    return not (r.party and inv.party and r.party != inv.party)


def _pairs(
    receipts: list[Receipt], invoices: list[Invoice], window: int, need_party: bool
) -> list[tuple[Receipt, Invoice]]:
    return [(r, inv) for r in receipts for inv in invoices if _pairable(r, inv, window, need_party)]


class _Pool:
    """The receipts and invoices not yet matched, and the groups taken so far."""

    def __init__(self, receipts: list[Receipt], invoices: list[Invoice]) -> None:
        self.groups: list[Group] = []
        self.free_r = {r.key: r for r in receipts}
        self.free_i = {i.key: i for i in invoices}

    def take(self, group: Group) -> None:
        self.groups.append(group)
        for r in group.receipts:
            self.free_r.pop(r.key, None)
        for i in group.invoices:
            self.free_i.pop(i.key, None)

    def free(self) -> tuple[list[Receipt], list[Invoice]]:
        return list(self.free_r.values()), list(self.free_i.values())

    def confirmed(self, raw_bank: list[Key], raw_invoices: list[Key]) -> Group | None:
        """The confirmed group, or None when a line is no longer free or currencies differ."""
        # the keys come from JSON as lists: back to (file_id, sheet, row)
        bank_keys = list(dict.fromkeys(cast(Key, tuple(k)) for k in raw_bank))
        invoice_keys = list(dict.fromkeys(cast(Key, tuple(k)) for k in raw_invoices))
        if not all(k in self.free_r for k in bank_keys):
            return None
        if not all(k in self.free_i for k in invoice_keys):
            return None
        rs = tuple(self.free_r[k] for k in bank_keys)
        iv = tuple(self.free_i[k] for k in invoice_keys)
        if len({r.currency for r in rs} | {i.currency for i in iv}) != 1:
            return None
        return Group("confirmed", rs, iv)


def match(
    receipts: list[Receipt],
    invoices: list[Invoice],
    window: int,
    confirmed: list[tuple[list[Key], list[Key]]] | None = None,
) -> list[Group]:
    """The matched groups, in pass order. `confirmed` are (bank keys, invoice keys) the user
    confirmed; one whose lines are no longer all free is skipped."""
    pool = _Pool(receipts, invoices)
    for raw_bank, raw_invoices in confirmed or []:
        group = pool.confirmed(raw_bank, raw_invoices)
        if group is not None:
            pool.take(group)
    for group in _reference_groups(*pool.free()):
        pool.take(group)
    pairs = _pairs(*pool.free(), window, True)
    pairs.sort(
        key=lambda p: (abs((p[0].day - p[1].day).days), p[1].day, p[1].number, p[0].day, p[0].key)
    )
    for r, inv in pairs:
        if r.key in pool.free_r and inv.key in pool.free_i:
            pool.take(Group("party_amount_date", (r,), (inv,)))
    pairs = _pairs(*pool.free(), window, False)
    by_r: dict[Key, int] = {}
    by_i: dict[Key, int] = {}
    for r, inv in pairs:
        by_r[r.key] = by_r.get(r.key, 0) + 1
        by_i[inv.key] = by_i.get(inv.key, 0) + 1
    for r, inv in pairs:
        if by_r[r.key] == 1 and by_i[inv.key] == 1:
            pool.take(Group("amount_date", (r,), (inv,)))
    return pool.groups
