"""Bank receipts against sales invoices: the fixed matching passes (DILIGENCE_PLAN.md D5).

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


def _reference_groups(receipts: list[Receipt], invoices: list[Invoice]) -> list[Group]:
    count: dict[str, int] = {}
    for inv in invoices:
        count[inv.number.upper()] = count.get(inv.number.upper(), 0) + 1
    unique = [inv for inv in invoices if count[inv.number.upper()] == 1]
    parent: dict[Key, Key] = {}

    def find(k: Key) -> Key:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    r_by = {r.key: r for r in receipts}
    i_by = {i.key: i for i in unique}
    used: set[Key] = set()
    for r in receipts:
        for inv in unique:
            if _names(r.text, inv.number):
                for k in (r.key, inv.key):
                    parent.setdefault(k, k)
                parent[find(r.key)] = find(inv.key)
                used |= {r.key, inv.key}
    clusters: dict[Key, list[Key]] = {}
    for k in sorted(used):
        clusters.setdefault(find(k), []).append(k)
    groups = []
    for keys in clusters.values():
        rs = tuple(r_by[k] for k in keys if k in r_by)
        iv = tuple(i_by[k] for k in keys if k in i_by)
        if len({r.currency for r in rs} | {i.currency for i in iv}) == 1:
            groups.append(Group("reference", rs, iv))
    return groups


def _pairs(
    receipts: list[Receipt], invoices: list[Invoice], window: int, need_party: bool
) -> list[tuple[Receipt, Invoice]]:
    out = []
    for r in receipts:
        for inv in invoices:
            if inv.currency != r.currency or inv.amount != r.amount:
                continue
            if abs((r.day - inv.day).days) > window:
                continue
            if need_party and (r.party is None or r.party != inv.party):
                continue
            if not need_party and r.party and inv.party and r.party != inv.party:
                continue
            out.append((r, inv))
    return out


def match(
    receipts: list[Receipt],
    invoices: list[Invoice],
    window: int,
    confirmed: list[tuple[list[Key], list[Key]]] | None = None,
) -> list[Group]:
    """The matched groups, in pass order. `confirmed` are (bank keys, invoice keys) the user
    confirmed; one whose lines are no longer all free is skipped."""
    groups: list[Group] = []
    free_r = {r.key: r for r in receipts}
    free_i = {i.key: i for i in invoices}

    def take(group: Group) -> None:
        groups.append(group)
        for r in group.receipts:
            free_r.pop(r.key, None)
        for i in group.invoices:
            free_i.pop(i.key, None)

    for bank_keys, invoice_keys in confirmed or []:
        if all(tuple(k) in free_r for k in bank_keys) and all(
            tuple(k) in free_i for k in invoice_keys
        ):
            take(
                Group(
                    "confirmed",
                    tuple(free_r[tuple(k)] for k in bank_keys),
                    tuple(free_i[tuple(k)] for k in invoice_keys),
                )
            )
    for group in _reference_groups(list(free_r.values()), list(free_i.values())):
        take(group)
    pairs = _pairs(list(free_r.values()), list(free_i.values()), window, True)
    pairs.sort(
        key=lambda p: (abs((p[0].day - p[1].day).days), p[1].day, p[1].number, p[0].day, p[0].key)
    )
    for r, inv in pairs:
        if r.key in free_r and inv.key in free_i:
            take(Group("party_amount_date", (r,), (inv,)))
    pairs = _pairs(list(free_r.values()), list(free_i.values()), window, False)
    by_r: dict[Key, int] = {}
    by_i: dict[Key, int] = {}
    for r, inv in pairs:
        by_r[r.key] = by_r.get(r.key, 0) + 1
        by_i[inv.key] = by_i.get(inv.key, 0) + 1
    for r, inv in pairs:
        if by_r[r.key] == 1 and by_i[inv.key] == 1:
            take(Group("amount_date", (r,), (inv,)))
    return groups
