"""Bank flow checks (SPEC-19): the pattern of money moving in and out of the company's accounts,
on the bank lines of every loaded statement (`fin.forensic.bank`). Each flags only amounts at or
above `min_amount` (the query threshold by default); `bank_cash` flags a month whose cash total is.
Account numbers are `ACCT-` tokens (SPEC-17); a party is the register token the remarks of a line
name, as the bank reconciliation reads it. Defaults and why: docs/decisions/forensic.md.

near_misses: a match one window's 20% too late (a return after `flow_days`, an own-account line
after OWN_DAYS, a reversal after `reversal_days`); a cash month from 80% of `min_amount`.
"""

import math
import re
from datetime import date, timedelta
from decimal import Decimal

from fin.diligence.bank_mask import TOKEN
from fin.forensic.bank import Bank, BankLine, money
from fin.forensic.books import Context
from fin.forensic.registry import Flag, ForensicTest, Outcome
from fin.forensic.settings import Setting
from fin.reconcile.base import rows_text

SETTINGS = (
    Setting(
        "flow_days",
        "whole",
        7,
        "money out and the same amount back on one account within a week",
    ),
    Setting(
        "cash_words",
        "names",
        ("CASH DEP", "CASH WDL", "ATM", "SELF"),
        "the words Indian bank statements print on cash deposits and withdrawals; add the "
        "company's bank's own",
    ),
)
OWN_DAYS = 3  # a transfer between the company's own accounts lands within 3 days
NEAR = Decimal("0.8")
KINDS = ("bank",)


def _bank(ctx: Context) -> Bank:
    if ctx.books.bank is None:  # pragma: no cover - the runner loads the lines
        raise RuntimeError("the bank lines are not loaded")
    return ctx.books.bank


def _wider(days: int) -> int:
    return max(math.ceil(days * 1.2), days + 1)


def _key(x: BankLine) -> tuple[int, str, int]:
    return (x.file_id, x.sheet, x.row)


def _pairs(bank: Bank, account: str, days: int) -> dict[tuple, tuple[BankLine, BankLine]]:
    """first line's key -> (first, second): money out and the same amount back (or in, then
    out) on the account within `days`, each line in one pair, in date order."""
    lines = [x for x, _ in bank.account(account)]
    used: set[tuple] = set()
    out = {}
    for i, x in enumerate(lines):
        if _key(x) in used:
            continue
        for y in lines[i + 1 :]:
            if (y.day - x.day).days > days:
                break
            if _key(y) not in used and y.side != x.side and y.amount == x.amount:
                used |= {_key(x), _key(y)}
                out[_key(x)] = (x, y)
                break
    return out


def _round_trip(x: BankLine, back: BankLine) -> Flag:
    party = x.party if x.party and x.party == back.party else None
    named = f"; both lines name {party}" if party else ""
    return x.flag(
        f"{money(x.amount)} {'out' if x.debit else 'in'} on {x.day:%d-%b-%Y} and "
        f"the same amount {'back' if x.debit else 'out'} on {back.day:%d-%b-%Y} "
        f"({back.source}), {(back.day - x.day).days} days later, on {x.account}{named}",
        party=party,
    )


def bank_round_trip(ctx: Context) -> Outcome:
    bank, days = _bank(ctx), ctx.settings["flow_days"]
    mine = [x for x in bank.lines(ctx.period) if x.amount >= ctx.min_amount]
    flags, near = [], 0
    for account in sorted({x.account for x in mine}):
        pairs = _pairs(bank, account, days)
        later = _pairs(bank, account, _wider(days))
        paired = {_key(line) for pair in pairs.values() for line in pair}
        for x in (x for x in mine if x.account == account):
            if _key(x) in pairs:
                flags.append(_round_trip(*pairs[_key(x)]))
            elif _key(x) in later and _key(x) not in paired:
                near += 1
    return Outcome(flags=flags, near_misses=near, population=len(mine))


def _named(x: BankLine) -> list[str]:
    return sorted({t for t in TOKEN.findall(x.text) if t != x.account})


def _gaps(bank: Bank, x: BankLine, other: str) -> list[int]:
    """Days between x and each line of its amount on the other account, up to the wider window."""
    gaps = (abs((y.day - x.day).days) for y, _ in bank.account(other) if y.amount == x.amount)
    return [g for g in gaps if g <= _wider(OWN_DAYS)]


def bank_own_transfer(ctx: Context) -> Outcome:
    bank = _bank(ctx)
    flags, near, population = [], 0, 0
    for x in bank.lines(ctx.period):
        if x.amount < ctx.min_amount:
            continue
        for other in _named(x):
            covering = [s for s in bank.statements if s.account == other and s.covers(x.day)]
            if not covering:
                continue  # that account's statement for the date is not loaded: nothing to compare
            population += 1
            gaps = _gaps(bank, x, other)
            if any(g <= OWN_DAYS for g in gaps):
                continue
            if gaps:
                near += 1
                continue
            flags.append(
                x.flag(
                    f"a {x.side} of {money(x.amount)} on {x.day:%d-%b-%Y} on {x.account} names "
                    f"{other}; no line of {money(x.amount)} on {other} within {OWN_DAYS} days, "
                    f"though its statement ({covering[0].file_name}) covers the date"
                )
            )
    return Outcome(flags=flags, near_misses=near, population=population)


def _early_debits(
    bank: Bank, period: str, first: date, days: int
) -> dict[tuple[str, Decimal], list[BankLine]]:
    """(account, amount) -> the period's debits in its first `days` days, in date order."""
    debits: dict[tuple[str, Decimal], list[BankLine]] = {}
    for y in sorted(bank.lines(period), key=lambda y: (y.day, y.row)):
        if y.debit and 0 <= (y.day - first).days < days:
            debits.setdefault((y.account, y.amount), []).append(y)
    return debits


def bank_period_end_credit(ctx: Context) -> Outcome:
    bank = _bank(ctx)
    following = bank.next(ctx.period)
    if following is None or not bank.of(following):
        return Outcome.cannot("the next period's bank statements (to find a reversal)")
    days, back = ctx.settings["period_end_days"], ctx.settings["reversal_days"]
    end, first = bank.bounds[ctx.period][1], bank.bounds[following][0]
    debits = _early_debits(bank, following, first, _wider(back))
    covered = {s.account for s in bank.of(following) if s.covers(first)}
    unchecked: set[str] = set()  # no next-period statement from its first day: not checked
    flags, near, population = [], 0, 0
    for x in bank.lines(ctx.period):
        last_days = end - timedelta(days=days) < x.day <= end
        if not (x.credit and x.amount >= ctx.min_amount and last_days):
            continue
        if x.account not in covered:
            unchecked.add(x.account)
            continue
        population += 1
        found = debits.get((x.account, x.amount), [])
        soon = [y for y in found if (y.day - first).days < back]
        if soon:
            y = soon[0]
            flags.append(
                x.flag(
                    f"a credit of {money(x.amount)} on {x.day:%d-%b-%Y}, in the last {days} days "
                    f"of {ctx.period}, and a debit of the same amount on {y.day:%d-%b-%Y} "
                    f"({y.source}), in the first {back} days of {following}, on {x.account}"
                )
            )
        elif found:
            near += 1
    needs = (
        f"{', '.join(sorted(unchecked))}: no {following} statement from its first day to find a "
        "reversal in"
        if unchecked
        else None
    )
    return Outcome(flags=flags, near_misses=near, population=population, needs=needs)


def _cash(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(r"\s+".join(map(re.escape, w.split())) for w in words)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])", re.IGNORECASE)


def bank_cash(ctx: Context) -> Outcome:
    pattern = _cash(ctx.settings["cash_words"])
    months: dict[tuple[str, date], list[BankLine]] = {}
    for x in _bank(ctx).lines(ctx.period):
        if pattern.search(x.text):
            months.setdefault((x.account, x.day.replace(day=1)), []).append(x)
    flags: list[Flag] = []
    near = 0
    for (account, month), lines in sorted(months.items()):
        ins = [x for x in lines if x.credit]
        outs = [x for x in lines if x.debit]
        deposits = sum((x.credit for x in ins), Decimal(0))
        withdrawals = sum((x.debit for x in outs), Decimal(0))
        total = deposits + withdrawals
        if total < ctx.min_amount:
            near += total >= ctx.min_amount * NEAR
            continue
        first = min(lines, key=lambda x: (x.day, x.row))
        refs = [(x.file_name, x.sheet, x.row) for x in sorted(lines, key=lambda x: (x.day, x.row))]
        flags.append(
            Flag(
                first.file_id,
                first.sheet,
                first.row,
                rows_text(refs) or first.source,
                f"cash on {account} in {month:%b-%Y}: deposits {money(deposits)} ({len(ins)} "
                f"lines), withdrawals {money(withdrawals)} ({len(outs)} lines), "
                f"{money(total)} in all",
                total,
                entry_date=first.day,
                page=first.page,
                account=account,
            )
        )
    return Outcome(flags=flags, near_misses=near, population=sum(len(v) for v in months.values()))


TESTS = (
    ForensicTest(
        "bank_round_trip",
        "money out and the same amount back (or in, then out) on one account within flow_days",
        ("min_amount", "flow_days"),
        bank_round_trip,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_own_transfer",
        "a line naming another of the company's accounts with no line of its amount there",
        ("min_amount",),
        bank_own_transfer,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_period_end_credit",
        "a credit in the last days of a period debited again early in the next",
        ("min_amount", "period_end_days", "reversal_days"),
        bank_period_end_credit,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_cash",
        "cash deposits and withdrawals by account and month (lines with a cash_words word)",
        ("min_amount", "cash_words"),
        bank_cash,
        kinds=KINDS,
    ),
)
