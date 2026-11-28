"""The FDD report's Forensic tests section on the mock set (SPEC-21), for mock/EXPECTED.md: the
flags of each test and period, counted off the planted rows and the rows of the books the tests
rightly flag, and the planted journals in the amount order the section lists them."""

from collections import Counter

from mock.generator import bank_extra
from mock.generator import books as b
from mock.generator import data as d
from mock.generator import data_counterparty as cp


def flag_counts(books: b.Books) -> Counter[tuple[str, str]]:
    """(test, period) -> the flags `fin forensic run corvane` gives with everything loaded."""
    counts: Counter[tuple[str, str]] = Counter()
    for planted in d.FORENSIC:
        counts[(planted.test, b.period_of(planted.when))] += len(planted.flags)
    counts.update((e[0], e[1]) for e in d.FORENSIC_EXISTING)
    counts.update((e[0], e[1]) for e in bank_extra.expected_flags(books))
    counts.update((e[0], e[1]) for e in cp.FLAGS)
    return +counts  # the near misses add none


def planted_order() -> list[tuple[str, str, str, int | None]]:
    """(test, voucher, ledger, amount) of the planted journals' flags, by test, then largest
    amount first; a voucher gap (the missing number, no ledger or amount) last."""
    rows = [
        (p.test, flag, "-", None) if p.test == "je_gap" else (p.test, p.number, flag, p.amount)
        for p in d.FORENSIC
        for flag in p.flags
    ]
    return sorted(rows, key=lambda r: (r[0], r[3] is None, -(r[3] or 0), r[1], r[2]))


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(row) + " |" for row in rows]


def section(books: b.Books) -> list[str]:
    out = [
        "",
        "## The FDD report's Forensic tests section (SPEC-21)",
        "",
        "With everything above loaded and `fin forensic run corvane` run, `fin report corvane "
        "--fdd` has a Forensic tests section before Reconciliations and queries: every "
        "registered test for every period with its state as above (ran, stale, too few entries, "
        "cannot run and what it needs, or not run), then per test its flags at or above the "
        f"query threshold of {d.QUERY_THRESHOLD:,}, largest amount first and at most 20, each "
        "with its file and sheet!row, then the queries raised on flags and the final forensic "
        "findings (drafts too with --drafts). A party is its token; a ledger is named as filed. "
        "Flags of each test and period (the flagged count of its State; tests and periods not "
        "listed flag nothing):",
        "",
    ]
    counts = flag_counts(books)
    out += _table(
        ["Test", "Period", "Flags"], [[t, p, str(n)] for (t, p), n in sorted(counts.items())]
    )
    out += [
        "",
        "The planted journals' flags, in the order the section lists them within their test "
        "(the rows of the books a test rightly flags fall between them by amount; a voucher's two "
        "lines in the order the test flagged them):",
        "",
    ]
    rows = [
        [test, voucher, flag, "-" if amount is None else f"{amount:,}"]
        for test, voucher, flag, amount in planted_order()
    ]
    return out + _table(["Test", "Voucher", "Ledger", "Amount"], rows)
