"""The second account's statements (`mock/extra/bank/`) and the bank forensic part of
mock/EXPECTED.md (SPEC-19), from `data_bank`."""

from collections.abc import Callable
from datetime import date, datetime

from mock.generator import books as b
from mock.generator import data as d
from mock.generator import data_bank as k
from mock.generator.sources import Sheet, bank_statement

FIRST_ROW = 8  # six header lines and the column header above the first transaction


def _opening(statement: k.Statement) -> int:
    before = [r for r in k.ROWS if r.statement.start < statement.start]
    return k.OPENING + sum(r.credit - r.debit for r in before)


def statement(statement: k.Statement) -> list[Sheet]:
    """The ICICI layout of the main account's statements, for the second account."""
    rows = [r for r in k.ROWS if r.statement == statement]
    opening = _opening(statement)
    closing = opening + sum(r.credit - r.debit for r in rows)
    s = Sheet("Statement")
    for line in (
        "ICICI Bank - Statement of Account",
        f"Account Number : {k.ACCOUNT}",
        "Account Currency : INR",
        f"Transactions Period: {statement.start:%d/%m/%Y} to {statement.end:%d/%m/%Y}",
        f"Opening Balance : {opening:,.2f}",
        f"Closing Balance : {closing:,.2f}",
    ):
        s.add([line])
    s.add(
        [
            "S No.",
            "Value Date",
            "Transaction Date",
            "Cheque Number",
            "Transaction Remarks",
            "Withdrawal Amount (INR)",
            "Deposit Amount (INR)",
            "Balance (INR)",
        ]
    )
    balance = opening
    for n, r in enumerate(rows, start=1):
        balance += r.credit - r.debit
        when = r.when.strftime("%d/%m/%Y")
        printed = None if r.blank else balance + r.off
        s.add([n, when, when, r.reference, r.remarks, r.debit or None, r.credit or None, printed])
    return [s]


def extra() -> dict[str, Callable[[], list[Sheet]]]:
    """mock/extra/ name -> the sheets."""
    return {f"bank/{s.name}": (lambda s=s: statement(s)) for s in k.STATEMENTS}  # type: ignore[misc]


def row_of(row: k.Row) -> int:
    rows = [r for r in k.ROWS if r.statement == row.statement]
    return FIRST_ROW + next(i for i, r in enumerate(rows) if r is row)  # two rows are equal


def existing(books: b.Books) -> list[tuple[str, str, str, int, date]]:
    """(test, period, file, row, date) of the main account's rows a default flags (`k.EXISTING`)."""
    test, text, _ = k.EXISTING
    out = []
    for period in ("FY24", "FY25", d.STUB):
        name = f"icici_{d.BANK_ACCOUNT}_{period.lower().replace('-ytd-sep', '_h1')}.xlsx"
        for n, row in enumerate(bank_statement(books, period)[0].rows, start=1):
            if isinstance(row[0], int) and text in str(row[4]):
                when = datetime.strptime(str(row[1]), "%d/%m/%Y").date()
                out.append((test, period, name, n, when))
    return out


def expected_flags(books: b.Books) -> set[tuple[str, str, str, int | None, object]]:
    """(test, period, statement file, row or None, date) of every flag the bank tests give."""
    out: set[tuple[str, str, str, int | None, object]] = set(existing(books)) | {
        (r.test, r.statement.period, r.statement.name, row_of(r), r.when)
        for r in k.ROWS
        if r.flagged
    }
    out |= {
        (k.GAP, s.period, s.name, None, first)
        for s, first, _, why in k.GAPS
        if not why.startswith("near miss")
    }
    return out


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(row) + " |" for row in rows]


def _amount(r: k.Row) -> str:
    return f"{r.debit or r.credit:,} {'debit' if r.debit else 'credit'}"


def section(books: b.Books) -> list[str]:
    """The bank forensic tests' flags on the mock set."""
    out = [
        "## Forensic tests on bank statements (SPEC-19)",
        "",
        "The three statements of the main account (`03_bank/`) and the two of a second account, "
        f"{k.ACCOUNT} (`extra/bank/`: not in the books or the data room, so load them only for "
        "these tests: they would change bank_vs_tb FY25), with the registers loaded (a party is "
        "named by its token) and the query threshold of "
        f"{d.QUERY_THRESHOLD:,}: `fin forensic run corvane` flags exactly these rows and no "
        "near miss. The main account flags only its yearly self-assessment tax payment (the "
        "default cash word SELF; marked for tuning); its uncleared cheque (paid 31-Mar-2025, "
        "cleared 03-Apr-2025) is not a period-end reversal. FY23 has no bank statement (cannot "
        "run), and the stub has no next period for bank_period_end_credit (cannot run). A row "
        "of the stub statement prints no balance: the chain restarts there and it is not flagged.",
        "",
    ]
    for test in sorted({r.test for r in k.ROWS if r.test} | {k.GAP}):
        flagged = [
            [r.statement.period, f"{r.statement.name} Statement!{row_of(r)}", f"{r.when:%d-%b-%Y}",
             _amount(r), r.why]
            for r in k.ROWS if r.test == test and r.flagged
        ] + [
            [s.period, f"{s.name} (statement period)", f"{a:%d-%b-%Y} to {z:%d-%b-%Y}", "-", why]
            for s, a, z, why in k.GAPS if test == k.GAP and not why.startswith("near miss")
        ] + [
            [p, f"{name} Statement!{n}", f"{when:%d-%b-%Y}", "-", k.EXISTING[2]]
            for t, p, name, n, when in existing(books) if t == test
        ]  # fmt: skip
        near = [
            [r.statement.period, f"{r.statement.name} Statement!{row_of(r)}", r.why]
            for r in k.ROWS if r.test == test and not r.flagged
        ] + [
            [s.period, f"{s.name} {a:%d-%b-%Y} to {z:%d-%b-%Y}", why]
            for s, a, z, why in k.GAPS if test == k.GAP and why.startswith("near miss")
        ]  # fmt: skip
        out += [f"### {test}", ""]
        out += _table(["Period", "Row", "Date", "Amount", "Why"], sorted(flagged))
        if near:
            out += ["", "Near misses (not flagged):", ""]
            out += _table(["Period", "Row", "Why"], sorted(near))
        out.append("")
    return out
