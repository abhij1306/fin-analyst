"""Bank row checks (SPEC-19), per account and statement, on the lines fin loads from each bank
statement (`fin.forensic.bank`). The single-row tests (balance chain, date order, duplicates) flag
only amounts at or above `min_amount` (the query threshold by default). Defaults and why:
docs/decisions/forensic.md.

near_misses: an amount from 80% of `min_amount`; a month with one round line fewer than needed; a
stretch with no line from 80% of `gap_days`.
"""

import math
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

from fin.forensic.bank import BankLine, Statement, money
from fin.forensic.books import Context
from fin.forensic.registry import Flag, ForensicTest, Outcome
from fin.forensic.settings import Setting

SETTINGS = (
    Setting(
        "gap_days",
        "whole",
        31,
        "a month with no transaction inside a statement's period; an operating account moves "
        "money every month",
    ),
)
NEAR = Decimal("0.8")
ROUND_LINES = 3  # a month needs this many round lines on one account
KINDS = ("bank",)


def statements(ctx: Context) -> list[Statement]:
    return ctx.books.bank.of(ctx.period) if ctx.books.bank else []


def _near(ctx: Context, amount: Decimal) -> bool:
    return ctx.min_amount * NEAR <= amount < ctx.min_amount


def bank_balance_chain(ctx: Context) -> Outcome:
    found = statements(ctx)
    without = [s for s in found if all(x.balance is None for x in s.lines)]
    names = ", ".join(f"{s.account} {s.start:%d-%b-%Y} to {s.end:%d-%b-%Y}" for s in without)
    needs = f"the statement {names}: it prints no row balance" if without else None
    if len(without) == len(found):
        return Outcome.cannot(f"row balances on the period's bank statements ({names}: none)")
    flags, near, population = [], 0, 0
    for st in found:
        if st in without:
            continue
        previous: Decimal | None = st.opening
        for x in st.lines:
            population += 1
            if x.balance is None:  # the chain restarts at the next row with a balance
                previous = None
                continue
            if previous is not None:
                expected = previous + x.credit - x.debit
                gap = x.balance - expected
                if abs(gap) >= ctx.min_amount:
                    flags.append(
                        x.flag(
                            f"balance {money(x.balance)} on {x.account}, expected {money(expected)}"
                            f" (the balance above {money(previous)} + credit {money(x.credit)} - "
                            f"debit {money(x.debit)}); {money(abs(gap))} apart",
                            gap,
                        )
                    )
                elif gap and _near(ctx, abs(gap)):
                    near += 1
            previous = x.balance
    return Outcome(flags=flags, near_misses=near, population=population, needs=needs)


def bank_date_order(ctx: Context) -> Outcome:
    flags, near, population = [], 0, 0
    for st in statements(ctx):
        for above, x in pairwise(st.lines):
            population += 1
            if x.day >= above.day:
                continue
            if x.amount >= ctx.min_amount:
                flags.append(
                    x.flag(
                        f"a {x.side} dated {x.day:%d-%b-%Y} below a row dated {above.day:%d-%b-%Y} "
                        f"({above.source}) on {x.account}"
                    )
                )
            elif _near(ctx, x.amount):
                near += 1
    return Outcome(flags=flags, near_misses=near, population=population)


def bank_duplicate(ctx: Context) -> Outcome:
    flags, near, population = [], 0, 0
    for st in statements(ctx):
        population += len(st.lines)
        groups: dict[tuple, list[BankLine]] = {}
        for x in st.lines:
            groups.setdefault((x.day, x.side, x.amount, x.remarks, x.reference), []).append(x)
        for lines in groups.values():
            if len(lines) < 2:
                continue
            for x in lines:
                others = ", ".join(str(o.row) for o in lines if o is not x)
                if x.amount >= ctx.min_amount:
                    flags.append(
                        x.flag(
                            f"the same {x.side} of {money(x.amount)} on {x.day:%d-%b-%Y} with the "
                            f"same remarks and reference as row(s) {others} on {x.account}"
                        )
                    )
                elif _near(ctx, x.amount):
                    near += 1
    return Outcome(flags=flags, near_misses=near, population=population)


def bank_round(ctx: Context) -> Outcome:
    unit = ctx.settings["round_unit"]
    months: dict[tuple[str, str], list[BankLine]] = {}
    population = 0
    for st in statements(ctx):
        population += len(st.lines)
        for x in st.lines:
            if x.amount % unit == 0:
                months.setdefault((x.account, f"{x.day:%Y-%m}"), []).append(x)
    flags: list[Flag] = []
    near = 0
    for (account, _), lines in sorted(months.items()):
        if len(lines) < ROUND_LINES:
            near += len(lines) == ROUND_LINES - 1
            continue
        flags += [
            x.flag(
                f"one of {len(lines)} lines on {account} in {x.day:%b-%Y} that are multiples of "
                f"{money(unit)}"
            )
            for x in lines
        ]
    return Outcome(flags=flags, near_misses=near, population=population)


def _stretches(st: Statement) -> list[tuple[date, date]]:
    """(first, last) day of each run of days with no line inside the statement's period."""
    days = sorted({x.day for x in st.lines if st.covers(x.day)})
    edges = [st.start - timedelta(days=1), *days, st.end + timedelta(days=1)]
    return [
        (a + timedelta(days=1), b - timedelta(days=1))
        for a, b in pairwise(edges)
        if (b - a).days > 1
    ]


def bank_date_gap(ctx: Context) -> Outcome:
    most = ctx.settings["gap_days"]
    low = math.ceil(most * NEAR)
    flags, near, population = [], 0, 0
    for st in statements(ctx):
        population += len(st.lines)
        sheet = st.lines[0].sheet if st.lines else None
        for first, last in _stretches(st):
            days = (last - first).days + 1
            if days > most:
                flags.append(
                    Flag(
                        st.file_id,
                        sheet,
                        None,
                        f"{st.file_name}: statement {st.start:%d-%b-%Y} to {st.end:%d-%b-%Y}",
                        f"no line on {st.account} from {first:%d-%b-%Y} to {last:%d-%b-%Y} "
                        f"({days} days, more than {most}) inside the statement's period",
                        entry_date=first,
                        account=st.account,
                    )
                )
            elif days >= low:
                near += 1
    return Outcome(flags=flags, near_misses=near, population=population)


TESTS = (
    ForensicTest(
        "bank_balance_chain",
        "bank rows whose balance is not the balance above plus the credit less the debit",
        ("min_amount",),
        bank_balance_chain,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_date_order",
        "bank rows dated before the row above them",
        ("min_amount",),
        bank_date_order,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_duplicate",
        "bank rows with the same date, side, amount, remarks and reference",
        ("min_amount",),
        bank_duplicate,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_round",
        "months with three or more bank lines that are multiples of round_unit",
        ("round_unit",),
        bank_round,
        kinds=KINDS,
    ),
    ForensicTest(
        "bank_date_gap",
        "a stretch of more than gap_days with no bank line inside a statement's period",
        ("gap_days",),
        bank_date_gap,
        kinds=KINDS,
    ),
)
