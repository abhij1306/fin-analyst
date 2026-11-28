"""SPEC-19 on synthetic statements: the cases the mock does not reach (a statement with no row
balances, a chain that restarts at a row with none, an own-account line whose other account is not
covered, the user's cash words) and the words of a bank test's state."""

from datetime import date, timedelta
from decimal import Decimal

from fin.forensic import settings
from fin.forensic.bank import Bank, BankLine, Statement
from fin.forensic.books import Books, Context
from fin.forensic.families.bank_flows import bank_cash, bank_own_transfer
from fin.forensic.families.bank_rows import bank_balance_chain
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


def test_a_row_with_no_balance_restarts_the_chain() -> None:
    rows = [
        line(0, credit=500000, balance=500000),
        line(1, debit=200000),
        line(2, debit=100000, balance=900000),
    ]
    outcome = bank_balance_chain(context([statement(rows)]))

    assert outcome.outcome == "ran" and not outcome.flags and outcome.population == 3


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


def test_cash_words_are_the_users() -> None:
    rows = [
        line(0, credit=170000, remarks="BY CASH 0412"),
        line(1, debit=60000, remarks="cash dep"),
    ]

    assert bank_cash(context([statement(rows)])).flags == []
    [flag] = bank_cash(context([statement(rows)], cash_words=("BY CASH",))).flags
    assert flag.amount == Decimal(170000) and flag.account == "ACCT-0001"
