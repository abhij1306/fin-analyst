"""SPEC-19 on synthetic statements: the cases the mock does not reach (a statement with no row
balances, a chain that runs on through a row with none, an own-account line whose other account is
not covered, the user's cash words, a period-end credit whose account the next period does not cover
from its first day, a statement not in rupees) and the words of a bank test's state."""

from datetime import date, timedelta
from decimal import Decimal

from fin.forensic import registry, settings
from fin.forensic.bank import Bank, BankLine, Statement
from fin.forensic.books import Books, Context
from fin.forensic.families.bank_flows import bank_cash, bank_own_transfer, bank_period_end_credit
from fin.forensic.families.bank_rows import bank_balance_chain
from fin.forensic.run import _outcome
from fin.forensic.status import state_text

START, END = date(2024, 4, 1), date(2025, 3, 31)
ZERO = Decimal(0)


def line(
    n: int,
    debit: int = 0,
    credit: int = 0,
    balance: int | None = None,
    *,
    file_id: int = 1,
    account: str = "ACCT-0001",
    remarks: str = "NEFT",
    day: date | None = None,
) -> BankLine:
    return BankLine(
        file_id, f"s{file_id}.xlsx", "Statement", None, n + 8, account,
        day or START + timedelta(days=n), remarks, "", Decimal(debit), Decimal(credit),
        None if balance is None else Decimal(balance), None,
    )  # fmt: skip


def statement(lines: list[BankLine], file_id: int = 1, opening: int = 0, **kw: object) -> Statement:
    account = lines[0].account if lines else "ACCT-0001"
    start, end = kw.get("start", START), kw.get("end", END)
    return Statement(
        file_id, f"s{file_id}.xlsx", "FY25", account, start, end, Decimal(opening), tuple(lines)
    )  # type: ignore[arg-type]


def context(statements: list[Statement], **changed: object) -> Context:
    values = {**settings.values({}, "acme"), **changed}
    bank = Bank(statements, {"FY25": (START, END)})
    books = Books(["FY25"], {}, {}, {}, {}, "h", {"FY25": {"bank"}}, bank=bank, bank_hash="b")
    return Context(books, "FY25", values, Decimal(100000))


def test_a_statement_with_no_row_balances_cannot_run() -> None:
    outcome = bank_balance_chain(context([statement([line(0, credit=500000)])]))

    assert outcome.outcome == "cannot_run"
    assert (
        outcome.needs
        == "row balances on the period's bank statements (ACCT-0001 01-Apr-2024 to 31-Mar-2025: "
        "none)"
    )


def test_another_statement_still_runs_and_names_the_one_without() -> None:
    good = statement(
        [line(0, credit=500000, balance=500000), line(1, debit=200000, balance=100000)]
    )
    bare = statement([line(0, credit=1, file_id=2)], file_id=2)
    outcome = bank_balance_chain(context([good, bare]))

    [flag] = outcome.flags
    assert flag.row == 9 and flag.amount == Decimal(-200000)
    assert "expected ₹300,000.00" in flag.reason
    row = {
        "outcome": "ran",
        "state": "ran",
        "flagged": 1,
        "over_threshold": 1,
        "needs": outcome.needs,
    }
    assert state_text(row).endswith(
        "; cannot run for the statement ACCT-0001 01-Apr-2024 to 31-Mar-2025: it prints no row "
        "balance"
    )


def test_a_row_with_no_balance_carries_the_expected_balance() -> None:
    rows = [
        line(0, credit=500000, balance=500000),
        line(1, debit=200000),
        line(2, debit=100000, balance=200000),
    ]
    outcome = bank_balance_chain(context([statement(rows)]))

    assert outcome.outcome == "ran" and not outcome.flags and outcome.population == 3
    assert (outcome.needs or "").startswith("a balance on 1 row(s) (s1.xlsx Statement!9)")


def test_an_own_account_line_is_compared_only_where_the_other_statement_covers_it() -> None:
    out = line(0, debit=400000, remarks="TRF TO ACCT-0002")
    other = statement(
        [line(0, credit=1000, account="ACCT-0002", file_id=2, day=date(2024, 10, 1))],
        file_id=2,
        start=date(2024, 10, 1),
        end=END,
    )

    assert bank_own_transfer(context([statement([out]), other])).flags == []
    later = line(200, debit=400000, remarks="TRF TO ACCT-0002")
    [flag] = bank_own_transfer(context([statement([later]), other])).flags
    assert "names ACCT-0002; no line of ₹400,000.00 on ACCT-0002 within 3 days" in flag.reason


def test_a_period_end_credit_on_an_account_with_no_next_statement_is_named_not_passed() -> None:
    nxt = (date(2025, 4, 1), date(2026, 3, 31))
    credit_a = line(0, credit=500000, day=date(2025, 3, 30))
    credit_b = line(0, credit=500000, day=date(2025, 3, 30), account="ACCT-0002", file_id=2)
    back_a = line(0, debit=500000, day=date(2025, 4, 2), file_id=3)
    statements = [statement([credit_a]), statement([credit_b], file_id=2)]
    following = Statement(3, "s3.xlsx", "FY26", "ACCT-0001", *nxt, ZERO, (back_a,))
    ctx = context([*statements, following])
    ctx.books.bank = Bank([*statements, following], {"FY25": (START, END), "FY26": nxt})

    outcome = bank_period_end_credit(ctx)

    assert [(f.account, f.row) for f in outcome.flags] == [("ACCT-0001", 8)]
    assert outcome.population == 1
    assert outcome.needs == "ACCT-0002: no FY26 statement from its first day to find a reversal in"


def test_a_next_statement_starting_after_the_window_does_not_count_as_checked() -> None:
    late = (date(2025, 5, 1), date(2026, 3, 31))
    credit = line(0, credit=500000, day=date(2025, 3, 30))
    statements = [statement([credit])]
    following = Statement(3, "s3.xlsx", "FY26", "ACCT-0001", *late, ZERO, ())
    ctx = context([*statements, following])
    ctx.books.bank = Bank(
        [*statements, following], {"FY25": (START, END), "FY26": (date(2025, 4, 1), late[1])}
    )

    outcome = bank_period_end_credit(ctx)

    assert outcome.flags == []
    assert outcome.population == 0
    assert outcome.needs == "ACCT-0001: no FY26 statement from its first day to find a reversal in"


def test_a_statement_in_another_currency_is_left_out_and_named() -> None:
    rupees = statement([line(1, debit=300000), line(0, debit=300000, day=START)])
    dollars = Statement(
        2, "s2.xlsx", "FY25", "ACCT-0003", START, END, ZERO,
        (line(1, debit=300000, file_id=2, account="ACCT-0003"),
         line(0, debit=300000, file_id=2, account="ACCT-0003", day=START)),
        "USD",
    )  # fmt: skip
    ctx = context([rupees])
    ctx.books.bank = Bank([rupees], {"FY25": (START, END)}, [dollars])

    outcome = _outcome(registry.tests()["bank_date_order"], ctx, ctx.books)

    assert [(f.account, f.row) for f in outcome.flags] == [("ACCT-0001", 8)]
    assert outcome.needs == (
        "the statement ACCT-0003 (USD) 01-Apr-2024 to 31-Mar-2025: not in rupees, like the "
        "thresholds"
    )


def test_cash_words_are_the_users() -> None:
    rows = [
        line(0, credit=170000, remarks="BY CASH 0412"),
        line(1, debit=60000, remarks="cash dep"),
    ]

    assert bank_cash(context([statement(rows)])).flags == []
    [flag] = bank_cash(context([statement(rows)], cash_words=("BY CASH",))).flags
    assert flag.amount == Decimal(170000) and flag.account == "ACCT-0001"
