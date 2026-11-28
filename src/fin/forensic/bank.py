"""The bank statement lines of an engagement (SPEC-19), for the bank forensic tests.

Each loaded statement (`bank_statements`, `bank_lines`) is kept with its lines in file order (sheet
or page, then row), its account token (`ACCT-0001`, SPEC-17) and its period. A line's party is the
register token of the first customer or vendor its remarks or reference name
(`fin.diligence.mask.Parties`, as the bank reconciliation reads them). Remarks are as filed.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.diligence.mask import Parties, party_tokens
from fin.forensic.registry import Flag
from fin.reconcile.bank_lines import period_bounds

ZERO = Decimal(0)


def money(value: Decimal) -> str:
    return f"₹{value:,.2f}"


@dataclass(frozen=True)
class BankLine:
    file_id: int
    file_name: str
    sheet: str
    page: int | None
    row: int
    account: str
    day: date
    remarks: str
    reference: str
    debit: Decimal
    credit: Decimal
    balance: Decimal | None
    party: str | None

    @property
    def side(self) -> str:
        return "debit" if self.debit else "credit"

    @property
    def amount(self) -> Decimal:
        return self.debit or self.credit

    @property
    def text(self) -> str:
        return f"{self.remarks} {self.reference}".strip()

    @property
    def source(self) -> str:
        return f"{self.file_name} {self.sheet}!{self.row}"

    def flag(self, reason: str, amount: Decimal | None = None, party: str | None = None) -> Flag:
        return Flag(
            self.file_id,
            self.sheet,
            self.row,
            self.source,
            reason,
            amount if amount is not None else self.amount,
            entry_date=self.day,
            page=self.page,
            account=self.account,
            party=party,
        )


@dataclass(frozen=True)
class Statement:
    """One loaded statement: its period (the engagement's), the dates it covers, its opening
    balance and its lines in file order."""

    file_id: int
    file_name: str
    period: str
    account: str
    start: date
    end: date
    opening: Decimal
    lines: tuple[BankLine, ...]
    currency: str = "INR"
    # every row of the balance chain in file order: the lines and the rows with no movement that
    # print a balance (left out of every other test); empty: the lines
    chain: tuple[BankLine, ...] = ()

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def rows(self) -> tuple[BankLine, ...]:
        return self.chain or self.lines


def order(line: BankLine, statement: Statement) -> tuple[Any, ...]:
    """A line's place among an account's lines, whatever order the files were loaded in."""
    return (line.day, statement.start, statement.end, line.file_name, line.sheet, line.row)


@dataclass
class Bank:
    """The rupee statements the tests read. A statement in another currency is left out (the
    thresholds are rupees, and a flag's amount is stored in rupees) and named by `left_out`."""

    statements: list[Statement]
    bounds: dict[str, tuple[date, date]]  # each engagement period's first and last day
    foreign: list[Statement] = field(default_factory=list)

    def left_out(self, period: str) -> str | None:
        found = [s for s in self.foreign if s.period == period]
        names = ", ".join(
            f"{s.account} ({s.currency}) {s.start:%d-%b-%Y} to {s.end:%d-%b-%Y}" for s in found
        )
        return f"the statement {names}: not in rupees, like the thresholds" if found else None

    def of(self, period: str) -> list[Statement]:
        return [s for s in self.statements if s.period == period]

    def lines(self, period: str) -> list[BankLine]:
        return [x for s in self.of(period) for x in s.lines]

    def account(self, account: str) -> list[tuple[BankLine, Statement]]:
        """Every line of the account, in date order (then by statement and row)."""
        found = [(x, s) for s in self.statements if s.account == account for x in s.lines]
        return sorted(found, key=lambda pair: order(*pair))

    def accounts(self) -> set[str]:
        return {s.account for s in self.statements}

    def next(self, period: str) -> str | None:
        """The engagement period that starts the day after this one ends."""
        end = self.bounds[period][1]
        return next((p for p, (a, _) in self.bounds.items() if (a - end).days == 1), None)


STATEMENTS = """
SELECT b.file_id, s.file_name, s.period, b.account, b.period_start, b.period_end,
    b.opening_balance, b.currency
FROM bank_statements b JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = ? ORDER BY b.file_id
"""
LINES = """
SELECT sheet, page, "row", account, txn_date, coalesce(remarks, ''), coalesce(reference, ''),
    debit, credit, balance
FROM bank_lines WHERE file_id = ? AND (debit <> 0 OR credit <> 0 OR balance IS NOT NULL)
ORDER BY page NULLS FIRST, sheet, "row"
"""


def file_ids(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[int]:
    return [
        f
        for (f,) in con.execute(
            "SELECT file_id FROM source_files WHERE engagement_id = ? AND kind = 'bank' "
            "AND status = 'loaded' ORDER BY file_id",
            [engagement_id],
        ).fetchall()
    ]


def bank_hash(con: duckdb.DuckDBPyConnection, engagement_id: int) -> str:
    """sha256 of the loaded statements' contents and the party tokens a reason can name."""
    ids = file_ids(con, engagement_id)
    marks = ", ".join("?" for _ in ids) or "NULL"
    files = con.execute(
        f"SELECT file_id, sha256 FROM source_files WHERE file_id IN ({marks}) "  # noqa: S608
        "ORDER BY file_id",
        ids,
    ).fetchall()
    text = json.dumps([files, party_tokens(con)], default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any]) -> Bank:
    parties = Parties(con)
    statements, foreign = [], []
    rows = con.execute(STATEMENTS, [engagement["engagement_id"]]).fetchall()
    for file_id, name, period, account, start, end, opening, currency in rows:
        chain = []
        for sheet, page, row, acct, day, remarks, reference, debit, credit, balance in con.execute(
            LINES, [file_id]
        ).fetchall():
            party = parties.find(f"{reference} {remarks}")
            line = BankLine(
                file_id, name, sheet, page, row, acct, day, remarks, reference, debit, credit,
                balance, party,
            )  # fmt: skip
            chain.append(line)
        lines = tuple(x for x in chain if x.debit or x.credit)
        st = Statement(
            file_id, name, period, account, start, end, opening, lines, currency, tuple(chain)
        )
        (statements if currency == "INR" else foreign).append(st)
    return Bank(statements, period_bounds(engagement), foreign)
