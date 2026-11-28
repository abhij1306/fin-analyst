"""The forensic defects found in the audit of 2026-11-28, each on synthetic data: a blank voucher
number, a GSTR-2B note against the register, a party first masked as an employee, party tokens in
the books hash, the period-end and reversal windows, the balance chain and a statement left out."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from fin.db.schema import in_memory
from fin.diligence.mask import Parties
from fin.extract.profiles import norm
from fin.forensic import bank as forensic_bank
from fin.forensic import registry, settings
from fin.forensic.bank import Bank, BankLine, Statement
from fin.forensic.books import Books, Context, Line, _hash
from fin.forensic.families.bank_flows import bank_period_end_credit
from fin.forensic.families.bank_rows import bank_balance_chain
from fin.forensic.families.journal_lines import je_reversed
from fin.forensic.run import _outcome
from fin.reconcile.gstr2b import _docs, _pairs

FY24 = (date(2023, 4, 1), date(2024, 3, 31))
FY25 = (date(2024, 4, 1), date(2025, 3, 31))
FY26 = (date(2025, 4, 1), date(2026, 3, 31))
ZERO = Decimal(0)
THRESHOLD = Decimal(100000)


def values(**changed: object) -> dict[str, object]:
    return {**settings.values({}, "acme"), **changed}


def jline(row: int, ledger: str, day: date, debit: int = 0, credit: int = 0, **kw: object) -> Line:
    number = kw.get("number")
    return Line(
        int(kw.get("file_id", 1)), "lv.xlsx", "LV", row, ledger, day, "Journal",
        None if number is None else str(number), Decimal(debit), Decimal(credit),
    )  # fmt: skip


def test_lines_with_no_voucher_number_are_never_one_voucher() -> None:
    day = date(2024, 6, 3)
    lines = [
        jline(2, "Rent", day, debit=500000),
        jline(3, "Cash", day, credit=500000),
        jline(4, "Salary", day, debit=200000),
        jline(5, "Bank", day, credit=200000),
        jline(6, "Rent", day, debit=300000, number="JV/7"),
        jline(7, "Bank", day, credit=300000, number="JV/7"),
    ]
    books = Books(["FY25"], {"FY25": (1, "lv.xlsx", *FY25)}, {"FY25": lines}, {}, {}, "h")
    ctx = Context(books, "FY25", values(), THRESHOLD)

    whole, not_whole = ctx.whole_vouchers()

    assert [[x.row for x in v.lines] for v in whole] == [[6, 7]]
    assert not_whole == 4  # each blank-numbered line is its own voucher, never joined to another


def two_b(number: str, value: int, kind: str = "Regular") -> tuple[object, ...]:
    return ("2b.xlsx", "B2B", 3, "VEND-0001", number, date(2024, 5, 10), Decimal(value), kind)


def register(number: str, value: int) -> tuple[object, ...]:
    return ("reg.xlsx", "Purchase", 9, "VEND-0001", number, date(2024, 5, 10), Decimal(value), None)


def test_a_gstr2b_note_pairs_with_the_register_line_of_its_vendor_and_number() -> None:
    pairs = _pairs(_docs([two_b("CN-1", -5000, "Credit Note")]), _docs([register("CN-1", -5000)]))

    assert [(a.number, a.note, b.number) for a, b, _ in pairs if a and b] == [
        ("CN-1", "Credit Note", "CN-1")
    ]
    assert len(pairs) == 1


def test_a_note_is_never_added_to_an_invoice_with_the_same_number() -> None:
    left = _docs([two_b("INV-7", 9000), two_b("INV-7", -1000, "Credit Note")])
    pairs = _pairs(left, _docs([register("INV-7", 9000)]))

    found = {(a.note, b is not None) for a, b, _ in pairs if a}
    assert found == {("Credit Note", False), (None, True)} and len(pairs) == 2


@pytest.fixture
def company() -> Iterator[duckdb.DuckDBPyConnection]:
    with in_memory("company") as con:
        yield con


def mask(con: duckdb.DuckDBPyConnection, token: str, kind: str, name: str) -> None:
    con.execute(
        "INSERT INTO mask_values VALUES (?, ?, ?, ?, NULL, 'r')", [token, kind, name, norm(name)]
    )


def test_a_register_party_first_masked_as_an_employee_is_a_party(
    company: duckdb.DuckDBPyConnection,
) -> None:
    mask(company, "EMP-0001", "employee_name", "R. Shah")
    mask(company, "EMP-0002", "employee_name", "Only Staff")
    company.execute(
        'INSERT INTO register_lines (file_id, sheet, "row", kind, customer, invoice_no, '
        "invoice_date, amount_inr, run_id) VALUES (1, 'Sales', 2, 'sales', 'EMP-0001', 'S-1', "
        "'2024-05-01', 100, 'r')"
    )

    parties = Parties(company)

    assert parties.token("R.  shah") == "EMP-0001"
    assert parties.find("NEFT FROM R. SHAH 4411") == "EMP-0001"
    assert parties.token("Only Staff") is None  # an employee on no register line is no party


def test_a_new_party_token_changes_the_books_hash(company: duckdb.DuckDBPyConnection) -> None:
    before = _hash(company, [])
    mask(company, "VEND-0001", "vendor", "Acme Traders")

    assert _hash(company, []) != before


def two_years(lines_25: list[Line], lines_26: list[Line], files: dict | None = None) -> Context:  # type: ignore[type-arg]
    files = files or {"FY25": (1, "a.xlsx", *FY25), "FY26": (2, "b.xlsx", *FY26)}
    lines = {"FY25": lines_25, "FY26": lines_26}
    books = Books(list(files), files, {p: lines.get(p, []) for p in files}, {}, {}, "h")
    return Context(books, next(iter(files)), values(), THRESHOLD)


def test_a_reversal_dated_before_the_next_period_is_not_in_its_first_days() -> None:
    journal = jline(2, "Accrued income", date(2025, 3, 29), debit=400000, number="JV/9")
    early = jline(5, "Accrued income", date(2025, 3, 30), credit=400000, number="JV/1", file_id=2)
    later = jline(6, "Accrued income", date(2025, 4, 2), credit=400000, number="JV/2", file_id=2)

    assert je_reversed(two_years([journal], [early])).flags == []
    [flag] = je_reversed(two_years([journal], [early, later])).flags
    assert "reversed by Journal JV/2 on 02-Apr-2025" in flag.reason


def test_a_skipped_year_is_not_the_next_period() -> None:
    journal = jline(2, "Accrued income", date(2024, 3, 29), debit=400000, number="JV/9")
    back = jline(5, "Accrued income", date(2025, 4, 2), credit=400000, number="JV/1", file_id=2)
    files = {"FY24": (1, "a.xlsx", *FY24), "FY26": (2, "b.xlsx", *FY26)}
    ctx = two_years([], [back], files)
    ctx.books.lines["FY24"] = [journal]

    outcome = je_reversed(ctx)

    assert outcome.outcome == "cannot_run" and outcome.flags == []


def bline(row: int, day: date, debit: int = 0, credit: int = 0, **kw: object) -> BankLine:
    balance = kw.get("balance")
    return BankLine(
        int(kw.get("file_id", 1)), "s.xlsx", "Statement", None, row,
        str(kw.get("account", "ACCT-0001")), day, "NEFT", "", Decimal(debit), Decimal(credit),
        None if balance is None else Decimal(str(balance)), None,
    )  # fmt: skip


def bank_context(bank: Bank) -> Context:
    books = Books(["FY25"], {}, {}, {}, {}, "h", {"FY25": {"bank"}}, bank=bank, bank_hash="b")
    return Context(books, "FY25", values(), THRESHOLD)


def test_a_credit_after_the_period_end_is_not_a_period_end_credit() -> None:
    after = bline(8, date(2025, 4, 2), credit=500000)
    inside = bline(9, date(2025, 3, 30), credit=500000)
    back = bline(10, date(2025, 4, 3), debit=500000, file_id=2)
    this = Statement(1, "s.xlsx", "FY25", "ACCT-0001", *FY25, ZERO, (inside, after))
    following = Statement(2, "t.xlsx", "FY26", "ACCT-0001", *FY26, ZERO, (back,))
    bank = Bank([this, following], {"FY25": FY25, "FY26": FY26})

    outcome = bank_period_end_credit(bank_context(bank))

    assert [f.row for f in outcome.flags] == [9]
    assert outcome.population == 1


def test_a_missing_balance_is_named_and_the_chain_runs_on() -> None:
    rows = (
        bline(8, date(2024, 4, 2), debit=500000),
        bline(9, date(2024, 4, 3), debit=100000, balance=1000000),
    )
    st = Statement(1, "s.xlsx", "FY25", "ACCT-0001", *FY25, Decimal(1000000), rows)

    outcome = bank_balance_chain(bank_context(Bank([st], {"FY25": FY25})))

    [flag] = outcome.flags
    assert (flag.row, flag.amount) == (9, Decimal(600000))  # expected 1,000,000 - 500,000 - 100,000
    assert outcome.needs == (
        "a balance on 1 row(s) (s.xlsx Statement!8): those rows are not checked, the row after "
        "each is checked against the expected balance"
    )


def test_a_dated_row_with_no_movement_stays_in_the_balance_chain(
    company: duckdb.DuckDBPyConnection,
) -> None:
    company.execute(
        "INSERT INTO source_files VALUES (1, 1, 'bank', 's.xlsx', 'x', 'a', 'FY25', ?, ?, "
        "'audited', 'p', 'loaded', NULL, 'r', now())",
        list(FY25),
    )
    company.execute(
        "INSERT INTO bank_statements VALUES (1, 'B', 'ACCT-0001', 'INR', ?, ?, 1000000, 'header', "
        "1400000, 'header', 100000, 0, 2, true, 'excel', NULL, 'r')",
        list(FY25),
    )
    for row, debit, balance in ((8, 0, 1500000), (9, 100000, 1400000)):
        company.execute(
            "INSERT INTO bank_lines VALUES (1, 'Statement', NULL, ?, 'B', 'ACCT-0001', 'INR', "
            "'2024-05-02', 'NEFT', NULL, ?, 0, ?, 'r')",
            [row, debit, balance],
        )
    engagement = {"engagement_id": 1, "periods": "FY25", "fiscal_start_month": 4}

    bank = forensic_bank.load(company, engagement)

    assert [x.row for x in bank.lines("FY25")] == [9]  # the other tests see the movement only
    outcome = bank_balance_chain(bank_context(bank))
    # the break is the row that moved the balance with no movement, not the debit after it
    assert [(f.row, f.amount) for f in outcome.flags] == [(8, Decimal(500000))]
    assert outcome.population == 2


def test_cannot_run_names_the_foreign_statement_left_out() -> None:
    dollars = Statement(
        2, "s2.xlsx", "FY25", "ACCT-0003", *FY25, ZERO,
        (bline(8, date(2024, 5, 2), debit=300, balance=700, account="ACCT-0003"),), "USD",
    )  # fmt: skip
    ctx = bank_context(Bank([], {"FY25": FY25}, [dollars]))

    outcome = _outcome(registry.tests()["bank_balance_chain"], ctx, ctx.books)

    assert outcome.outcome == "cannot_run"
    assert outcome.needs == (
        "a loaded bank statement in rupees for the period; the statement ACCT-0003 (USD) "
        "01-Apr-2024 to 31-Mar-2025: not in rupees, like the thresholds"
    )
