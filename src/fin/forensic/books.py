"""The Ledger Vouchers lines of an engagement's periods, and the vouchers rebuilt from them.

Each voucher appears once in the block of every ledger it touches, so the lines of one file with the
same voucher type, number and date are one voucher (docs/decisions/forensic.md, question 2). A
rebuilt voucher whose debits do not equal its credits is not whole: the voucher tests leave it out
and count it. `particulars` is never read.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cached_property
from typing import Any

import duckdb

from fin.forensic import bank as forensic_bank
from fin.forensic.registry import Flag, ForensicTest
from fin.reconcile import periods_of
from fin.reconcile.base import loaded_file

ZERO = Decimal(0)


@dataclass(frozen=True)
class Line:
    file_id: int
    file_name: str
    sheet: str
    row: int
    ledger: str
    entry_date: date
    voucher_type: str | None
    voucher_no: str | None
    debit: Decimal
    credit: Decimal

    @property
    def side(self) -> str:
        return "debit" if self.debit else "credit"

    @property
    def amount(self) -> Decimal:
        return self.debit or self.credit

    @property
    def source(self) -> str:
        return f"{self.file_name} {self.sheet}!{self.row}"

    @property
    def voucher_key(self) -> tuple[Any, ...]:
        return (self.file_id, self.voucher_type, self.voucher_no, self.entry_date)

    def flag(self, reason: str, amount: Decimal | None = None) -> Flag:
        return Flag(
            self.file_id,
            self.sheet,
            self.row,
            self.source,
            reason,
            amount if amount is not None else self.amount,
            self.ledger,
            self.voucher_type,
            self.voucher_no,
            self.entry_date,
        )


@dataclass
class Voucher:
    lines: list[Line]

    @property
    def whole(self) -> bool:
        return sum((x.debit for x in self.lines), ZERO) == sum((x.credit for x in self.lines), ZERO)

    @property
    def first(self) -> Line:
        return self.lines[0]


@dataclass
class Books:
    """What the engagement has loaded: per period, its Ledger Vouchers file (file_id, name, first
    day, last day), lines and the Tally group of each ledger (from the period's trial balance), and
    the confirmed ledger mappings."""

    periods: list[str]
    files: dict[str, tuple[int, str, date, date]]
    lines: dict[str, list[Line]]
    groups: dict[str, dict[str, str]]
    accounts: dict[str, str]
    data_hash: str
    loaded: dict[str, set[str]] = field(default_factory=dict)  # period -> loaded file kinds
    _vouchers: dict[str, list[Voucher]] = field(default_factory=dict)
    bank: forensic_bank.Bank | None = None  # the bank statements (SPEC-19)
    bank_hash: str = ""

    def hash_for(self, test: ForensicTest) -> str:
        """The data a test reads, as one hash: the bank statements for a bank test, else the
        Ledger Vouchers, trial balances and confirmed mappings."""
        return self.bank_hash if "bank" in test.kinds else self.data_hash

    def previous(self, period: str) -> str | None:
        """The loaded period that ends last before this one starts."""
        start = self.files[period][2]
        before = [p for p, f in self.files.items() if f[3] < start]
        return max(before, key=lambda p: self.files[p][3]) if before else None

    def next(self, period: str) -> str | None:
        end = self.files[period][3]
        after = [p for p, f in self.files.items() if f[2] > end]
        return min(after, key=lambda p: self.files[p][2]) if after else None

    def vouchers(self, period: str) -> list[Voucher]:
        if period not in self._vouchers:
            grouped: dict[tuple[Any, ...], list[Line]] = {}
            for line in self.lines.get(period, []):
                grouped.setdefault(line.voucher_key, []).append(line)
            self._vouchers[period] = [Voucher(lines) for lines in grouped.values()]
        return self._vouchers[period]


@dataclass
class Context:
    """One test on one period: the books, the settings and the query threshold."""

    books: Books
    period: str
    settings: dict[str, Any]
    threshold: Decimal

    @property
    def lines(self) -> list[Line]:
        return self.books.lines.get(self.period, [])

    @property
    def start(self) -> date:
        return self.books.files[self.period][2]

    @property
    def end(self) -> date:
        return self.books.files[self.period][3]

    @cached_property
    def min_amount(self) -> Decimal:
        value = self.settings["min_amount"]
        return self.threshold if value == "query_threshold" else Decimal(value)

    def journal(self, line: Line) -> bool:
        return (line.voucher_type or "") in self.settings["journal_types"]

    def account(self, ledger: str) -> str:
        """The confirmed account of a ledger, else the ledger itself."""
        return self.books.accounts.get(ledger, ledger)

    def whole_vouchers(self) -> tuple[list[Voucher], int]:
        """(the period's whole vouchers, how many are not whole)."""
        all_ = self.books.vouchers(self.period)
        whole = [v for v in all_ if v.whole]
        return whole, len(all_) - len(whole)


def _hash(con: duckdb.DuckDBPyConnection, file_ids: list[int]) -> str:
    """sha256 of the loaded files' contents and the confirmed mappings."""
    marks = ", ".join("?" for _ in file_ids) or "NULL"
    files = con.execute(
        f"SELECT file_id, sha256 FROM source_files WHERE file_id IN ({marks}) "  # noqa: S608
        "ORDER BY file_id",
        file_ids,
    ).fetchall()
    maps = con.execute(
        "SELECT ledger, account FROM ledger_accounts WHERE status = 'confirmed' ORDER BY ledger"
    ).fetchall()
    text = json.dumps([files, maps], default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def without_lines(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any]) -> Books:
    """The books with their hashes and no lines (`fin forensic status`)."""
    return load(con, engagement, lines=False)


def load(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], lines: bool = True) -> Books:
    eid = engagement["engagement_id"]
    periods = periods_of(engagement)
    files: dict[str, tuple[int, str, date, date]] = {}
    trial: dict[str, int] = {}
    for period in periods:
        if found := loaded_file(con, eid, "ledger_vouchers", period):
            files[period] = found
        if tb := loaded_file(con, eid, "trial_balance", period):
            trial[period] = tb[0]
    kinds: dict[str, set[str]] = {}
    for kind, period in con.execute(
        "SELECT DISTINCT kind, period FROM source_files WHERE engagement_id = ? "
        "AND status = 'loaded' AND period IS NOT NULL",
        [eid],
    ).fetchall():
        kinds.setdefault(period, set()).add(kind)
    books = Books(
        periods,
        files,
        {},
        {},
        dict(
            con.execute(
                "SELECT ledger, account FROM ledger_accounts WHERE status = 'confirmed'"
            ).fetchall()
        ),
        _hash(con, sorted([f[0] for f in files.values()] + list(trial.values()))),
        kinds,
        bank_hash=forensic_bank.bank_hash(con, eid),
    )
    if not lines:
        return books
    books.bank = forensic_bank.load(con, engagement)
    for period, (file_id, name, _, _) in files.items():
        books.lines[period] = [
            Line(file_id, name, *row)
            for row in con.execute(
                'SELECT sheet, "row", ledger, entry_date, voucher_type, voucher_no, '
                "coalesce(debit, 0), coalesce(credit, 0) FROM ledger_entries WHERE file_id = ? "
                "AND (coalesce(debit, 0) <> 0 OR coalesce(credit, 0) <> 0) "
                'AND entry_date IS NOT NULL ORDER BY sheet, "row"',
                [file_id],
            ).fetchall()
        ]
    for period, file_id in trial.items():
        books.groups[period] = dict(
            con.execute(
                "SELECT ledger, any_value(group_name) FROM trial_balance WHERE file_id = ? "
                "AND row_type = 'ledger' AND group_name IS NOT NULL GROUP BY ledger",
                [file_id],
            ).fetchall()
        )
    return books
