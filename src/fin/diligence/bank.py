"""Bank statements, Excel and PDF (docs/diligence.md D3): `fin ingest <co> <file> --kind bank`.

A bank column profile (`templates/profiles/bank_*.toml`: ICICI, SBI, Axis; INR and USD accounts)
says how to read one bank's layout. An Excel statement is read by `fin.extract.ledger.read_table`;
a PDF by the PDF add-on the user chose (`fin.diligence.bank_pdf`). Both end as a `Raw` (the rows
with their sheet or page and row, and the header and summary text lines), which `read_statement`
turns into a `Statement`: the account, currency, period, opening and closing balance (from the
header lines, else the first row's balance minus its movement and the last row's balance) and the
transaction lines.

The statement is checked, never fixed (the rows stay as filed):

- it ties when opening + credits - debits = closing; when it does not, that is a finding
  (`bank_tie`) the user decides on, and G1 stays open until they do;
- statements of one account whose periods overlap, or whose balances do not join (a statement's
  opening is not the previous one's closing), are findings (`fin.diligence.bank_checks`).

`ingest_bank` checks the engagement and the file first (before any PDF page is sent to a parser),
reads the file, then in ONE transaction adds the `source_files` row (the period of the engagement
that holds every date of the statement; E808 otherwise), the `bank_statements` row, the
`bank_lines` (remarks and reference as filed) and the
findings. Any error rolls it back. Raises E101-E105, E204, E206, E803, E807, E808, E816-E818.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import BankHeaderMissing, ParseError, SourcePeriodInvalid
from fin.audit.logger import Run
from fin.config import Config
from fin.db.conn import connect, transaction
from fin.diligence import bank_checks, bank_pdf
from fin.diligence.bank_raw import Raw, RawRow
from fin.diligence.engagement import require_open
from fin.diligence.loading import (
    Loaded,
    add_file,
    archive_copy,
    insert_rows,
    log_load,
    refuse_duplicate,
)
from fin.diligence.stub import UNAUDITED
from fin.extract.ledger import amount, cell_text, parse_date, read_table
from fin.extract.periods import fiscal_year_of
from fin.extract.profiles import Profile
from fin.extract.units import parse_figure
from fin.findings import record
from fin.ingest import sha256_of
from fin.paths import company_db_path

KIND = "bank"


@dataclass(frozen=True)
class BankLine:
    row: RawRow
    date: date
    remarks: str
    reference: str
    debit: Decimal
    credit: Decimal
    balance: Decimal | None


@dataclass(frozen=True)
class Statement:
    profile: Profile
    account: str
    currency: str
    start: date
    end: date
    opening: Decimal
    opening_from: str  # 'header' or 'rows'
    closing: Decimal
    closing_from: str
    lines: list[BankLine]

    @property
    def debits(self) -> Decimal:
        return sum((x.debit for x in self.lines), Decimal("0.00"))

    @property
    def credits(self) -> Decimal:
        return sum((x.credit for x in self.lines), Decimal("0.00"))

    @property
    def computed_closing(self) -> Decimal:
        return self.opening + self.credits - self.debits

    @property
    def ties(self) -> bool:
        return self.computed_closing == self.closing

    @property
    def label(self) -> str:
        return f"{self.profile.bank} {self.account} {self.currency}"


def _find(pattern: re.Pattern[str] | None, lines: list[str]) -> str | None:
    """The `value` group of the first line the pattern matches."""
    if pattern is None:
        return None
    return next((m["value"] for line in lines if (m := pattern.search(line))), None)


def _money(text: str) -> Decimal:
    figure = parse_figure(text)
    assert figure is not None  # the header patterns only match figures
    return figure.quantize(Decimal("0.01"))


def _read_lines(raw: Raw, name: str) -> tuple[list[BankLine], list[str]]:
    """(the transaction lines, the header lines plus the text of each undated row)."""
    profile, lines, out = raw.profile, list(raw.lines), []
    for r in raw.rows:
        where = f"{name} {r.sheet}!{r.row}"
        when = parse_date(r.cells.get("date"), profile)
        if when is None:  # a heading, or a summary row ("Closing Balance 1,000.00")
            text = " ".join(t for v in r.cells.values() if (t := cell_text(v)))
            lines.extend([text] if text else [])
            continue
        balance = r.cells.get("balance")
        blank = balance is None or not cell_text(balance)
        out.append(
            BankLine(
                r,
                when,
                cell_text(r.cells.get("remarks")),
                cell_text(r.cells.get("reference")),
                amount(r.cells.get("debit"), profile, where),
                amount(r.cells.get("credit"), profile, where),
                None if blank else amount(balance, profile, where),
            )
        )
    return out, lines


def _edge_balance(
    header: str | None,
    row_balance: Decimal | None,
    name: str,
    profile: Profile,
    edge: tuple[str, str],
) -> tuple[Decimal, str]:
    """(balance, "header" or "rows") of an edge ("opening", "first" or "closing", "last")."""
    if header is not None:
        return _money(header), "header"
    if row_balance is not None:
        return row_balance, "rows"
    which, side = edge
    raise BankHeaderMissing(
        f"{name}: no {which} balance in the header lines and the {side} row has no balance "
        f"(profile {profile.name}, [header] {which}). Nothing loaded"
    )


def read_statement(raw: Raw, name: str) -> Statement:
    """The statement a `Raw` holds. E204 on a bad amount or no transaction row, E817 when the
    header gives no account number, or no opening balance and the first row has no balance."""
    profile = raw.profile
    out, lines = _read_lines(raw, name)
    if not out:
        raise ParseError(f"{name}: no transaction row (a dated row) below the column header")
    patterns = profile.header
    account = _find(patterns["account"], lines)
    if account is None:
        raise BankHeaderMissing(
            f"{name}: no account number in the header lines (profile {profile.name}, "
            f"[header] account = {patterns['account'].pattern!r}). Nothing loaded"
        )
    currency = (_find(patterns.get("currency"), lines) or profile.currency).upper()
    first, last = out[0], out[-1]
    derived = None if first.balance is None else first.balance - first.credit + first.debit
    opening_balance, opening_from = _edge_balance(
        _find(patterns.get("opening"), lines), derived, name, profile, ("opening", "first")
    )
    closing_balance, closing_from = _edge_balance(
        _find(patterns.get("closing"), lines), last.balance, name, profile, ("closing", "last")
    )
    return Statement(
        profile,
        account,
        currency,
        raw.start,
        raw.end,
        opening_balance,
        opening_from,
        closing_balance,
        closing_from,
        out,
    )


def tie_findings(st: Statement, name: str) -> list[dict[str, Any]]:
    """The `bank_tie` finding (columns of the `findings` table) of a statement that does not tie."""
    if st.ties:
        return []
    gap = st.computed_closing - st.closing
    return [
        {
            "file_name": name,
            "check_name": "bank_tie",
            "finding": "bank_statement_does_not_tie",
            "statement": "bank",
            "period": f"{st.start.isoformat()} to {st.end.isoformat()}",
            "label": st.label,
            "fin_value": st.computed_closing,
            "other_value": st.closing,
            "difference": gap,
            "fin_source": f"opening ({st.opening_from}) + credits - debits, {len(st.lines)} lines",
            "other_source": f"closing balance ({st.closing_from})",
            "message": (
                f"bank statement {st.label} {st.start} to {st.end}: opening {st.opening} + "
                f"credits {st.credits} - debits {st.debits} is {st.computed_closing}, but the "
                f"closing balance is {st.closing}; rows kept as filed"
            ),
        }
    ]


def bank_period(
    st: Statement, engagement: dict[str, Any], start_month: int, name: str
) -> tuple[str, str]:
    """(the engagement period holding every date of the statement, its basis): one annual period
    in scope ('audited') or the stub ('unaudited'). E808 otherwise."""
    stub = engagement.get("stub")
    if stub and engagement["stub_start"] <= st.start and st.end <= engagement["stub_end"]:
        return stub, "unaudited"
    periods = engagement["periods"].split(",")
    first, last = (fiscal_year_of(d, start_month) for d in (st.start, st.end))
    if first == last and first in periods:
        return first, "audited"
    scope = ", ".join(periods) + (f"; stub {stub}" if stub else "")
    raise SourcePeriodInvalid(
        f"{name}: its dates {st.start} to {st.end} are not inside one period of the open "
        f"engagement ({scope}); a statement must not cross a year end. Nothing loaded"
    )


def excel_raw(path: Path, profile: str | None) -> Raw:
    """An Excel statement as a `Raw`. E101/E102, E807, E808; E204 when two sheets hold a table."""
    table = read_table(path, KIND, profile)
    if len({r.sheet for r in table.rows}) > 1:
        raise ParseError(f"{path.name}: one bank statement per file; its sheets each hold a table")
    rows = [RawRow(r.sheet, None, r.row, r.cells) for r in table.rows]
    return Raw(table.profile, table.start, table.end, list(table.lines), rows, "excel", None)


def _insert(run: Run, con: Any, st: Statement, file_id: int, raw: Raw) -> None:
    profile = st.profile
    con.execute(
        "INSERT INTO bank_statements (file_id, bank, account, currency, period_start, period_end, "
        "opening_balance, opening_from, closing_balance, closing_from, total_debits, "
        "total_credits, line_count, ties, reader, parse_path, run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [file_id, profile.bank, st.account, st.currency, st.start, st.end, st.opening]
        + [st.opening_from, st.closing, st.closing_from, st.debits, st.credits, len(st.lines)]
        + [st.ties, raw.reader, raw.parse_path, run.run_id],
    )
    insert_rows(
        con,
        "bank_lines",
        'file_id, sheet, page, "row", bank, account, currency, txn_date, remarks, reference, '
        "debit, credit, balance, run_id",
        [
            [file_id, x.row.sheet, x.row.page, x.row.row, profile.bank, st.account, st.currency]
            + [x.date, x.remarks or None, x.reference or None]
            + [x.debit, x.credit, x.balance, run.run_id]
            for x in st.lines
        ],
    )


def ingest_bank(run: Run, config: Config, company: str, path: Path, profile: str | None) -> Loaded:
    """Load one bank statement (see the module docstring)."""
    name, sha256, db = path.name, sha256_of(path), company_db_path(run.root, company)
    with connect(db) as con:  # before any page of a PDF is sent to a parser
        require_open(con, company)
        refuse_duplicate(con, name, sha256)
    if path.suffix.lower() == ".pdf":
        raw = bank_pdf.read_pdf(run, config, path, sha256, profile)
    else:
        raw = excel_raw(path, profile)
    st = read_statement(raw, name)
    with connect(db, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        refuse_duplicate(con, name, sha256)
        period, basis = bank_period(st, engagement, config.fiscal_year_start_month, name)
        archive = archive_copy(run, company, path, sha256)
        file = add_file(
            run,
            con,
            engagement,
            (KIND, name, sha256, archive),
            (period, basis, st.start, st.end, st.profile.name),
        )
        file_id = file["file_id"]
        _insert(run, con, st, file_id, raw)
        found = tie_findings(st, name)
        record(
            run, company, con, found, "file_id = ? AND check_name = 'bank_tie'", [file_id], file_id
        )
        checked = bank_checks.record_all(run, company, con, engagement["engagement_id"])
    head = (
        f"loaded as source file_id {file_id} (bank {period}, profile {st.profile.name}, read by "
        f"{raw.reader})"
    )
    if basis == "unaudited":
        head += f"; {period} is the stub: {UNAUDITED}"
    summary = (
        f"bank statement {st.label}: {len(st.lines)} lines, {st.start} to {st.end}, opening "
        f"{st.opening} ({st.opening_from}), closing {st.closing} ({st.closing_from}), "
        f"{'ties' if st.ties else 'DOES NOT TIE'}"
    )
    lines = [head, summary]
    if found:
        lines.append(
            f"{found[0]['message']} (a finding, not a failure: `fin findings list {company}`)"
        )
    lines += [
        f"{f['message']} (a finding: `fin findings list {company}`)"
        for f in checked.get(file_id, [])
    ]
    return log_load(run, company, file, lines, archive)
