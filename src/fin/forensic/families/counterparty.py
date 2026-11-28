"""Counterparty tests (SPEC-20, the forensic standard's fraud-risk indicators): new and spiking
vendors, invoices just under an approval limit, year-end sales credited soon after, parties settled
against each other by journal, loans to parties that do not trade, and expenses capitalised.

Register tests read the register lines of the period (parties are tokens); ledger tests read the
rebuilt vouchers and join a ledger to its register token through the masked names
(`fin.diligence.mask.Parties`). Single-entry tests flag only amounts at or above `min_amount`.
Defaults and why: docs/decisions/forensic.md. near_misses counts what misses a threshold by less
than 20%, as for the journal tests.
"""

import math
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from statistics import median

from fin.forensic.books import Context, Invoice, Line
from fin.forensic.families.journal_lines import NEAR, _last_days, _money
from fin.forensic.registry import ForensicTest, Outcome
from fin.forensic.settings import Setting

SETTINGS = (
    Setting(
        "new_vendor_share",
        "fraction",
        Decimal("0.1"),
        "a new vendor with a tenth or more of the period's purchases",
    ),
    Setting("spike_factor", "number", Decimal(3), "a month at three times the vendor's median"),
    Setting(
        "spike_history",
        "whole",
        6,
        "the vendor's median needs six earlier months with purchases",
    ),
    Setting(
        "credit_note_types",
        "names",
        ("Credit Note",),
        "Tally's credit note voucher type; add the company's own names",
    ),
)
CREDIT_DAYS = 60  # the first days of the next period a credit note is looked for in (the spec)
LOANS = ("loans_current", "loans_non_current", "loans")
# under fixed_assets in the dictionary (templates/dictionary/schedule_iii.csv), and itself
FIXED = (
    "fixed_assets",
    "ppe_net",
    "capital_work_in_progress",
    "intangible_assets",
    "intangible_assets_under_development",
)
CASH = ("cash", "cash_and_bank_balances")
PARTY_ACCOUNTS = ("trade_receivables", "trade_payables")


def _ran(flags: list, near: int, population: int) -> Outcome:
    return Outcome(flags=flags, near_misses=near, population=population)


def _no_register(kind: str, period: str) -> Outcome:
    return Outcome.cannot(f"a loaded {kind} register with invoices in {period}")


def _total(lines: Iterable[Invoice]) -> Decimal:
    return sum((x.amount for x in lines), Decimal(0))


def cp_new_vendor(ctx: Context) -> Outcome:
    lines = ctx.invoices("purchase")
    if not lines:
        return _no_register("purchase", ctx.period)
    earlier = [p for p in ctx.books.earlier(ctx.period) if ctx.invoices("purchase", p)]
    if not earlier:
        return Outcome.cannot("a purchase register with invoices in an earlier period")
    seen = {x.party for p in earlier for x in ctx.invoices("purchase", p)}
    share, total = ctx.settings["new_vendor_share"], _total(lines)
    per: dict[str | None, list[Invoice]] = {}
    for x in lines:
        per.setdefault(x.party, []).append(x)
    flags, near = [], 0
    for party, mine in per.items():
        if party in seen or not total:
            continue
        amount = _total(mine)
        part = amount / total
        if part >= share and amount >= ctx.min_amount:
            flags.append(
                mine[0].flag(
                    f"vendor {party} is new in {ctx.period} (no invoice in {', '.join(earlier)}) "
                    f"with {part:.1%} of the period's purchases ({_money(amount)} of "
                    f"{_money(total)})",
                    amount,
                )
            )
        elif part >= share * NEAR and amount >= ctx.min_amount * NEAR:
            near += 1
    return _ran(flags, near, len(per))


def _month(day: date) -> date:
    return day.replace(day=1)


def cp_vendor_spike(ctx: Context) -> Outcome:
    lines = ctx.invoices("purchase")
    if not lines:
        return _no_register("purchase", ctx.period)
    least, factor = ctx.settings["spike_history"], ctx.settings["spike_factor"]
    months: dict[str | None, dict[date, list[Invoice]]] = {}
    for p in [*ctx.books.earlier(ctx.period), ctx.period]:
        for x in ctx.invoices("purchase", p):
            months.setdefault(x.party, {}).setdefault(_month(x.day), []).append(x)
    flags, near, tested, most = [], 0, 0, 0
    for party, by_month in months.items():
        for month in sorted({_month(x.day) for x in lines if x.party == party}):
            history = [_total(v) for m, v in by_month.items() if m < month]
            most = max(most, len(history))
            if len(history) < least:
                continue
            tested += 1
            amount, middle = _total(by_month[month]), Decimal(median(history))
            if amount < ctx.min_amount * NEAR or middle <= 0:  # no typical month to compare with
                continue
            if amount >= factor * middle and amount >= ctx.min_amount:
                top = max(by_month[month], key=lambda x: x.amount)
                flags.append(
                    top.flag(
                        f"vendor {party}: purchases of {_money(amount)} in {month:%b-%Y}, "
                        f"{amount / middle:.1f} times its median month of {_money(middle)} "
                        f"over {len(history)} earlier months",
                        amount,
                    )
                )
            elif amount >= factor * middle * NEAR:
                near += 1
    if not tested:
        return Outcome.too_few(most, least)
    return _ran(flags, near, tested)


def cp_under_limit(ctx: Context) -> Outcome:
    lines = ctx.invoices("purchase")
    if not lines:
        return _no_register("purchase", ctx.period)
    limits, band = ctx.settings["approval_limits"], ctx.settings["limit_band"]
    big = [x for x in lines if x.amount >= ctx.min_amount]

    def under(amount: Decimal, width: Decimal) -> Decimal | None:
        return next((lim for lim in limits if lim * (1 - width) <= amount < lim), None)

    flags = [
        x.flag(
            f"purchase invoice {x.number} of vendor {x.party}: {_money(x.amount)} is within "
            f"{band:.0%} below the approval limit {_money(limit)}"
        )
        for x in big
        if (limit := under(x.amount, band)) is not None
    ]
    near = sum(
        under(x.amount, band) is None and under(x.amount, band * Decimal("1.2")) is not None
        for x in big
    )
    return _ran(flags, near, len(big))


def cp_credit_note(ctx: Context) -> Outcome:
    sales = ctx.invoices("sales")
    if not sales:
        return _no_register("sales", ctx.period)
    following = ctx.books.next(ctx.period)
    first = ctx.books.files[following][2] if following else None
    if first is None or (first - ctx.end).days != 1:
        return Outcome.cannot("the next period's Ledger Vouchers (to find a credit note)")
    days, kinds = ctx.settings["period_end_days"], ctx.settings["credit_note_types"]
    wider = max(math.ceil(days * 1.2), days + 1)
    late = max(math.ceil(CREDIT_DAYS * 1.2), CREDIT_DAYS + 1)
    notes: dict[str, list[Line]] = {}
    for x in ctx.books.lines.get(following, []):
        party = ctx.party(x.ledger)
        early = (x.entry_date - first).days < late
        if (x.voucher_type or "") in kinds and x.credit and party and early:
            notes.setdefault(party, []).append(x)
    flags, near = [], 0
    for x in sales:
        if not _last_days(x.day, ctx.end, wider) or x.amount < ctx.min_amount * NEAR:
            continue
        found = [n for n in notes.get(x.party or "", []) if n.amount >= ctx.min_amount * NEAR]
        soon = [
            n
            for n in found
            if (n.entry_date - first).days < CREDIT_DAYS and n.amount >= ctx.min_amount
        ]
        if soon and _last_days(x.day, ctx.end, days) and x.amount >= ctx.min_amount:
            n = soon[0]
            flags.append(
                x.flag(
                    f"sales invoice {x.number} of customer {x.party} dated {x.day:%d-%b-%Y}, in "
                    f"the last {days} days of {ctx.period}, then {n.voucher_type} {n.voucher_no} "
                    f"of {_money(n.amount)} on {n.entry_date:%d-%b-%Y} ({n.source})"
                )
            )
        elif found:
            near += 1
    return _ran(flags, near, len(sales))


def _is_party(ctx: Context, ledger: str) -> bool:
    return ctx.party(ledger) is not None or ctx.account(ledger) in PARTY_ACCOUNTS


def cp_setoff(ctx: Context) -> Outcome:
    whole, not_whole = ctx.whole_vouchers()
    journals = [v for v in whole if ctx.journal(v.first)]
    flags, near = [], 0
    for voucher in journals:
        mine = [x for x in voucher.lines if _is_party(ctx, x.ledger)]
        debits = {x.ledger for x in mine if x.debit}
        credits = {x.ledger for x in mine if x.credit}
        if not any(d != c for d in debits for c in credits):
            continue
        other = {"debit": ", ".join(sorted(credits)), "credit": ", ".join(sorted(debits))}
        big = [x for x in mine if x.amount >= ctx.min_amount]
        flags += [
            x.flag(
                f"a journal settling one party against another: {x.side} to this party ledger, "
                f"the other side to {len(other[x.side].split(', '))} party ledger(s)"
            )
            for x in big
        ]
        near += not big and any(x.amount >= ctx.min_amount * NEAR for x in mine)
    out = _ran(flags, near, len(journals))
    out.not_whole = not_whole
    return out


def cp_loan_no_trade(ctx: Context) -> Outcome:
    trading = {x.party for p in ctx.books.periods for x in ctx.books.invoices.get(p, []) if x.party}
    if not trading:
        return Outcome.cannot("a loaded sales or purchase register (to see who trades)")
    loans = [x for x in ctx.lines if ctx.account(x.ledger) in LOANS]
    flags, near = [], 0
    for x in loans:
        parties = ctx.books.parties
        token = parties.find(x.ledger) if parties else None
        if token in trading or not x.debit:
            continue
        who = f"party {token}" if token else "a party"
        if x.amount >= ctx.min_amount:
            flags.append(
                x.flag(
                    f"a loan debit of {_money(x.amount)} to {ctx.account(x.ledger)}: {who} with no "
                    "sales or purchase in any loaded period"
                )
            )
        elif x.amount >= ctx.min_amount * NEAR:
            near += 1
    return _ran(flags, near, len(loans))


def cp_capitalised(ctx: Context) -> Outcome:
    whole, not_whole = ctx.whole_vouchers()
    flags, near, population = [], 0, 0
    for voucher in whole:
        assets = [x for x in voucher.lines if x.debit and ctx.account(x.ledger) in FIXED]
        if not assets:
            continue
        population += len(assets)
        counter = [x for x in voucher.lines if x.credit]
        if not counter or any(
            ctx.account(c.ledger) in CASH or _is_party(ctx, c.ledger) for c in counter
        ):
            continue
        against = ", ".join(sorted({ctx.account(c.ledger) for c in counter}))
        for x in assets:
            if x.amount >= ctx.min_amount:
                flags.append(
                    x.flag(
                        f"{_money(x.amount)} debited to {ctx.account(x.ledger)} against "
                        f"{against}: neither cash and bank nor a party"
                    )
                )
            elif x.amount >= ctx.min_amount * NEAR:
                near += 1
    out = _ran(flags, near, population)
    out.not_whole = not_whole
    return out


TESTS = (
    ForensicTest(
        "cp_new_vendor",
        "a vendor first seen in the period with new_vendor_share or more of its purchases",
        ("min_amount", "new_vendor_share"),
        cp_new_vendor,
        kinds=(),
    ),
    ForensicTest(
        "cp_vendor_spike",
        "a vendor's month at spike_factor times its median month",
        ("min_amount", "spike_factor", "spike_history"),
        cp_vendor_spike,
        kinds=(),
    ),
    ForensicTest(
        "cp_under_limit",
        "purchase invoices within limit_band below an approval limit the user states",
        ("min_amount", "approval_limits", "limit_band"),
        cp_under_limit,
        kinds=(),
    ),
    ForensicTest(
        "cp_credit_note",
        "a sales invoice in the last days of a year credited early in the next",
        ("min_amount", "period_end_days", "credit_note_types"),
        cp_credit_note,
    ),
    ForensicTest(
        "cp_setoff",
        "a journal debiting one party ledger and crediting another",
        ("min_amount", "journal_types"),
        cp_setoff,
    ),
    ForensicTest(
        "cp_loan_no_trade",
        "a loan to a party with no sales or purchase in any loaded period",
        ("min_amount",),
        cp_loan_no_trade,
    ),
    ForensicTest(
        "cp_capitalised",
        "a fixed asset debited against neither cash and bank nor a party",
        ("min_amount",),
        cp_capitalised,
    ),
)
