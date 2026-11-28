"""Journal-entry tests on voucher series, ledgers, account pairs and first digits (SPEC-18). They
read every voucher type and flag whatever the amount; each needs a minimum population
(docs/decisions/forensic.md) and reports "too few entries" below it. Pairs read vouchers rebuilt
from every ledger block (fin.forensic.books), never `particulars`.

near_misses: a series of 80% of `min_series` numbers with a gap; a ledger one line (20%) over
`rare_max_lines`; a pair seen in two vouchers; a group whose deviation is from 80% of `benford_mad`.
"""

import math
import re
from decimal import Decimal

from fin.forensic.books import Context, Line
from fin.forensic.registry import Flag, ForensicTest, Outcome
from fin.forensic.settings import Setting
from fin.reconcile.base import rows_text

SETTINGS = (
    Setting("min_series", "whole", 20, "a voucher series needs 20 numbers to show a gap"),
    Setting(
        "rare_max_lines", "whole", 2, "and only a ledger with no line in another loaded period"
    ),
    Setting(
        "min_population",
        "whole",
        500,
        "rare ledgers and unusual pairs need 500 lines in the period to mean anything",
    ),
    Setting(
        "benford_min_lines",
        "whole",
        1000,
        "published Benford work recommends 1,000 items or more per group",
    ),
    Setting(
        "benford_min_amount", "number", Decimal(10), "first digits of amounts of 10 rupees or more"
    ),
    Setting(
        "benford_mad",
        "fraction",
        Decimal("0.015"),
        "the published bound of nonconformity of the mean absolute deviation of first digits",
    ),
)
SERIES = re.compile(r"\d+$")  # the trailing number; what is before it is the prefix


def je_gap(ctx: Context) -> Outcome:
    least = ctx.settings["min_series"]
    series: dict[tuple, dict[int, str]] = {}
    for x in ctx.lines:
        if x.voucher_no and (found := SERIES.search(x.voucher_no)):
            key = (x.voucher_type, x.voucher_no[: found.start()])
            series.setdefault(key, {})[int(found[0])] = found[0]
    if not series or max(len(s) for s in series.values()) < least:
        return Outcome.too_few(max((len(s) for s in series.values()), default=0), least)
    file_id, name = ctx.books.files[ctx.period][:2]
    flags, near, population = [], 0, 0
    for (kind, prefix), numbers in sorted(series.items(), key=lambda s: (str(s[0][0]), s[0][1])):
        missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
        if len(numbers) < least:
            near += bool(missing) and len(numbers) >= math.ceil(least * 0.8)
            continue
        population += len(numbers)
        width = len(numbers[min(numbers)])
        low, high = (f"{prefix}{numbers[n]}" for n in (min(numbers), max(numbers)))
        for n in missing:
            number = f"{prefix}{n:0{width}d}"
            flags.append(
                Flag(
                    file_id,
                    None,
                    None,
                    f"{name}: {kind} series {low} to {high}",
                    f"{kind} {number} is missing from a series of {len(numbers)} numbers "
                    f"({low} to {high})",
                    voucher_type=kind,
                    voucher_no=number,
                )
            )
    return Outcome(flags=flags, near_misses=near, population=population)


def _others(ctx: Context) -> list[str]:
    return [p for p in ctx.books.lines if p != ctx.period]


def je_rare_ledger(ctx: Context) -> Outcome:
    least, most = ctx.settings["min_population"], ctx.settings["rare_max_lines"]
    if len(ctx.lines) < least:
        return Outcome.too_few(len(ctx.lines), least)
    elsewhere = {x.ledger for p in _others(ctx) for x in ctx.books.lines[p]}
    per: dict[str, list[Line]] = {}
    for x in ctx.lines:
        per.setdefault(x.ledger, []).append(x)
    lonely = {ledger: lines for ledger, lines in per.items() if ledger not in elsewhere}
    others = ", ".join(_others(ctx)) or "none"
    flags = [
        x.flag(
            f"one of {len(lines)} line(s) on this ledger in {ctx.period}; it has none in the "
            f"other loaded periods ({others})"
        )
        for lines in lonely.values()
        if len(lines) <= most
        for x in lines
    ]
    near = sum(
        most < len(lines) <= max(math.ceil(most * 1.2), most + 1) for lines in lonely.values()
    )
    return Outcome(flags=flags, near_misses=near, population=len(ctx.lines))


def _pairs(ctx: Context, lines: list[Line]) -> dict[tuple[str, str], Line]:
    """(account debited, account credited) -> the first debit line of the pair in the voucher."""
    debits = [x for x in lines if x.debit]
    credit_lines = [x for x in lines if x.credit]
    out: dict[tuple[str, str], Line] = {}
    for d in debits:
        for c in credit_lines:
            out.setdefault((ctx.account(d.ledger), ctx.account(c.ledger)), d)
    return out


def je_unusual_pair(ctx: Context) -> Outcome:
    least = ctx.settings["min_population"]
    whole, not_whole = ctx.whole_vouchers()
    if len(ctx.lines) < least:
        return Outcome.too_few(len(ctx.lines), least, not_whole)
    seen: dict[tuple[str, str], int] = {}
    for period in ctx.books.lines:
        for voucher in ctx.books.vouchers(period):
            if voucher.whole:
                for pair in _pairs(ctx, voucher.lines):
                    seen[pair] = seen.get(pair, 0) + 1
    flags, near = [], 0
    for voucher in whole:
        total = sum((x.debit for x in voucher.lines), Decimal(0))
        for (debit, credit), line in _pairs(ctx, voucher.lines).items():
            if seen[(debit, credit)] == 1:
                flags.append(
                    line.flag(
                        f"the only voucher in the loaded periods that debits {debit} and credits "
                        f"{credit} ({line.voucher_type} {line.voucher_no})",
                        total,
                    )
                )
            elif seen[(debit, credit)] == 2:
                near += 1
    return Outcome(flags=flags, near_misses=near, population=len(ctx.lines), not_whole=not_whole)


BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}


def je_benford(ctx: Context) -> Outcome:
    groups = ctx.books.groups.get(ctx.period)
    if groups is None:
        return Outcome.cannot("the period's trial balance (the Tally group of each ledger)")
    least, floor = ctx.settings["benford_min_lines"], ctx.settings["benford_min_amount"]
    bound = float(ctx.settings["benford_mad"])
    per: dict[str, list[Line]] = {}
    for x in ctx.lines:
        if x.amount >= floor and x.ledger in groups:
            per.setdefault(groups[x.ledger], []).append(x)
    tested = {g: lines for g, lines in per.items() if len(lines) >= least}
    if not tested:
        return Outcome.too_few(max((len(v) for v in per.values()), default=0), least)
    flags, near = [], 0
    for group, lines in sorted(tested.items()):
        by_digit: dict[int, list[Line]] = {}
        for x in lines:
            by_digit.setdefault(int(str(x.amount).lstrip("0.")[0]), []).append(x)
        share = {d: len(by_digit.get(d, [])) / len(lines) for d in BENFORD}
        mad = sum(abs(share[d] - BENFORD[d]) for d in BENFORD) / 9
        if mad <= bound:
            near += mad >= bound * 0.8
            continue
        for d in sorted(d for d in BENFORD if share[d] > BENFORD[d]):
            hits = by_digit[d]
            refs = [(x.file_name, x.sheet, x.row) for x in hits]
            flags.append(
                Flag(
                    hits[0].file_id,
                    hits[0].sheet,
                    hits[0].row,
                    rows_text(refs) or hits[0].source,
                    f"Tally group {group}: first digit {d} on {share[d]:.1%} of {len(lines)} lines "
                    f"against {BENFORD[d]:.1%} expected (mean absolute deviation {mad:.4f}, above "
                    f"{bound}); {len(hits)} lines",
                    sum((x.amount for x in hits), Decimal(0)),
                )
            )
    population = sum(len(v) for v in tested.values())
    return Outcome(flags=flags, near_misses=near, population=population)


TESTS = (
    ForensicTest(
        "je_gap",
        "missing numbers in a numeric voucher series per voucher type and prefix",
        ("min_series",),
        je_gap,
    ),
    ForensicTest(
        "je_rare_ledger",
        "a ledger with few lines in a period and none in any other loaded period",
        ("min_population", "rare_max_lines"),
        je_rare_ledger,
    ),
    ForensicTest(
        "je_unusual_pair",
        "an (account debited, account credited) pair seen in one voucher across the loaded periods",
        ("min_population",),
        je_unusual_pair,
    ),
    ForensicTest(
        "je_benford",
        "a Tally group whose first digits do not conform to Benford's law",
        ("benford_min_lines", "benford_min_amount", "benford_mad"),
        je_benford,
        kinds=("ledger_vouchers", "trial_balance"),
    ),
)
