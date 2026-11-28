"""Journal-entry tests on single lines (SPEC-18): each flags only amounts at or above the
engagement's query threshold (`min_amount`). Defaults and why: docs/decisions/forensic.md.

near_misses counts the rows that would flag but for a threshold they miss by less than 20%: an
amount from 80% of `min_amount`, a day or more past `period_end_days` or `reversal_days`, a band
20% wider than `limit_band`.
"""

import math
from datetime import date, timedelta
from decimal import Decimal

from fin.forensic.books import Context, Line
from fin.forensic.registry import Flag, ForensicTest, Outcome
from fin.forensic.settings import Setting

SETTINGS = (
    Setting(
        "journal_types",
        "names",
        ("Journal",),
        "manual entries; sales, payments and receipts are dated by invoices and the bank",
    ),
    Setting(
        "round_unit",
        "number",
        Decimal(1_000_000),
        "multiples of 1,000 and 1,00,000 are routine (rent, hosting, retainers)",
    ),
    Setting("weekend_days", "weekdays", ("Sunday",), "many Indian companies work on Saturdays"),
    Setting(
        "holidays",
        "dates",
        ("01-26", "08-15", "10-02"),
        "the national holidays with fixed dates, every year (MM-DD); add the company's own, or a "
        "single date as YYYY-MM-DD",
    ),
    Setting("period_end_days", "whole", 5, "the last days of a period"),
    Setting("reversal_days", "whole", 15, "the first days of the next period"),
    Setting("limit_band", "fraction", Decimal("0.02"), "within 2% below a limit the user states"),
    Setting(
        "approval_limits",
        "limits",
        None,
        "the company's approval limits, as the user states them; none is assumed",
    ),
    Setting(
        "sensitive_accounts",
        "names",
        ("revenue", "cash", "trade_receivables", "trade_payables"),
        "the accounts a journal should rarely touch: revenue, cash and bank, receivables and "
        "payables",
    ),
)
NEAR = Decimal("0.8")  # an amount from 80% of min_amount is just outside it


def _money(value: Decimal) -> str:
    return f"₹{value:,.2f}"


def _big(ctx: Context, lines: list[Line], journal: bool = True) -> tuple[list[Line], list[Line]]:
    """(lines at or above min_amount, lines just below it), journals only unless journal=False."""
    pool = [x for x in lines if ctx.journal(x) or not journal]
    low = ctx.min_amount * NEAR
    return [x for x in pool if x.amount >= ctx.min_amount], [
        x for x in pool if low <= x.amount < ctx.min_amount
    ]


def _ran(flags: list[Flag], near: int, population: int) -> Outcome:
    return Outcome(flags=flags, near_misses=near, population=population)


def je_round(ctx: Context) -> Outcome:
    unit = ctx.settings["round_unit"]
    big, near = _big(ctx, ctx.lines)
    flags = [
        x.flag(f"a journal {x.side} of {_money(x.amount)}, a multiple of {_money(unit)}")
        for x in big
        if x.amount % unit == 0
    ]
    return _ran(flags, sum(x.amount % unit == 0 for x in near), len(big))


def _month_end(day: date) -> bool:
    return (day + timedelta(days=1)).month != day.month


def _off_day(ctx: Context, day: date) -> str | None:
    if _month_end(day):
        return None
    holidays = ctx.settings["holidays"]
    if day.isoformat() in holidays or day.strftime("%m-%d") in holidays:
        return "a holiday the user keeps"
    name = day.strftime("%A")
    return f"a {name}" if name in ctx.settings["weekend_days"] else None


def je_weekend(ctx: Context) -> Outcome:
    big, near = _big(ctx, ctx.lines)
    flags = [
        x.flag(f"a journal dated {x.entry_date:%d-%b-%Y}, {why} (not a month end)")
        for x in big
        if (why := _off_day(ctx, x.entry_date))
    ]
    return _ran(flags, sum(bool(_off_day(ctx, x.entry_date)) for x in near), len(big))


def je_duplicate(ctx: Context) -> Outcome:
    groups: dict[tuple, list[Line]] = {}
    for x in ctx.lines:
        key = (x.ledger, x.entry_date, x.side, x.amount, x.voucher_type)
        groups.setdefault(key, []).append(x)
    flags, near = [], 0
    for lines in groups.values():
        numbers = {x.voucher_no for x in lines}
        if len(numbers) < 2:
            continue
        for x in lines:
            others = ", ".join(sorted(str(n) for n in numbers - {x.voucher_no}))
            if x.amount >= ctx.min_amount:
                flags.append(
                    x.flag(
                        f"the same {x.side} of {_money(x.amount)} to this ledger on "
                        f"{x.entry_date:%d-%b-%Y} as {x.voucher_type} {others}"
                    )
                )
            elif x.amount >= ctx.min_amount * NEAR:
                near += 1
    return _ran(flags, near, len(ctx.lines))


def _last_days(day: date, end: date, days: int) -> bool:
    return end - timedelta(days=days) < day <= end


def je_period_end(ctx: Context) -> Outcome:
    previous = ctx.books.previous(ctx.period)
    if previous is None:
        return Outcome.cannot("the previous period's Ledger Vouchers (to compare its last days)")
    days = ctx.settings["period_end_days"]
    wider = max(math.ceil(days * 1.2), days + 1)
    before = ctx.books.files[previous][3]
    seen = {x.ledger for x in ctx.books.lines[previous] if _last_days(x.entry_date, before, days)}
    big, _ = _big(ctx, ctx.lines)
    fresh = [x for x in big if x.ledger not in seen]
    flags = [
        x.flag(
            f"a journal in the last {days} days of {ctx.period} ({x.entry_date:%d-%b-%Y}) on a "
            f"ledger with no entry in the last {days} days of {previous}"
        )
        for x in fresh
        if _last_days(x.entry_date, ctx.end, days)
    ]
    near = sum(
        _last_days(x.entry_date, ctx.end, wider) and not _last_days(x.entry_date, ctx.end, days)
        for x in fresh
    )
    return _ran(flags, near, len(big))


def je_reversed(ctx: Context) -> Outcome:
    following = ctx.books.next(ctx.period)
    if following is None:
        return Outcome.cannot("the next period's Ledger Vouchers (to find a reversal)")
    days, back = ctx.settings["period_end_days"], ctx.settings["reversal_days"]
    wider = max(math.ceil(back * 1.2), back + 1)
    first = ctx.books.files[following][2]
    later: dict[tuple, list[Line]] = {}
    for x in ctx.books.lines[following]:
        if ctx.journal(x) and (x.entry_date - first).days < wider:
            later.setdefault((x.ledger, x.amount, x.side), []).append(x)
    big, _ = _big(ctx, ctx.lines)
    flags, near = [], 0
    for x in big:
        if not _last_days(x.entry_date, ctx.end, days):
            continue
        other = "credit" if x.side == "debit" else "debit"
        matches = later.get((x.ledger, x.amount, other), [])
        soon = [m for m in matches if (m.entry_date - first).days < back]
        if soon:
            m = soon[0]
            flags.append(
                x.flag(
                    f"a journal of {x.entry_date:%d-%b-%Y} reversed by {m.voucher_type} "
                    f"{m.voucher_no} on {m.entry_date:%d-%b-%Y} ({m.source})"
                )
            )
        elif matches:
            near += 1
    return _ran(flags, near, len(big))


def je_sensitive(ctx: Context) -> Outcome:
    accounts = ctx.settings["sensitive_accounts"]
    big, near = _big(ctx, ctx.lines)
    flags = [
        x.flag(f"a journal {x.side} to {ctx.account(x.ledger)}, an account journals rarely touch")
        for x in big
        if ctx.account(x.ledger) in accounts
    ]
    return _ran(flags, sum(ctx.account(x.ledger) in accounts for x in near), len(big))


def je_under_limit(ctx: Context) -> Outcome:
    limits, band = ctx.settings["approval_limits"], ctx.settings["limit_band"]
    big, _ = _big(ctx, ctx.lines, journal=False)

    def under(amount: Decimal, width: Decimal) -> Decimal | None:
        return next((lim for lim in limits if lim * (1 - width) <= amount < lim), None)

    flags = [
        x.flag(f"{_money(x.amount)} is within {band:.0%} below the approval limit {_money(limit)}")
        for x in big
        if (limit := under(x.amount, band)) is not None
    ]
    near = sum(
        under(x.amount, band) is None and under(x.amount, band * Decimal("1.2")) is not None
        for x in big
    )
    return _ran(flags, near, len(big))


TESTS = (
    ForensicTest(
        "je_round",
        "journal lines that are multiples of round_unit",
        ("min_amount", "journal_types", "round_unit"),
        je_round,
    ),
    ForensicTest(
        "je_weekend",
        "journal lines dated on a weekend day or a holiday, month ends excepted",
        ("min_amount", "journal_types", "weekend_days", "holidays"),
        je_weekend,
    ),
    ForensicTest(
        "je_duplicate",
        "lines with the same ledger, date, side, amount and voucher type under different numbers",
        ("min_amount",),
        je_duplicate,
    ),
    ForensicTest(
        "je_period_end",
        "journals in the last days of a period on a ledger with none in the last days before",
        ("min_amount", "journal_types", "period_end_days"),
        je_period_end,
    ),
    ForensicTest(
        "je_reversed",
        "a journal in the last days of a period reversed early in the next",
        ("min_amount", "journal_types", "period_end_days", "reversal_days"),
        je_reversed,
    ),
    ForensicTest(
        "je_sensitive",
        "journals touching revenue, cash and bank, receivables or payables",
        ("min_amount", "journal_types", "sensitive_accounts"),
        je_sensitive,
    ),
    ForensicTest(
        "je_under_limit",
        "amounts within limit_band below an approval limit the user states",
        ("min_amount", "approval_limits", "limit_band"),
        je_under_limit,
    ),
)
