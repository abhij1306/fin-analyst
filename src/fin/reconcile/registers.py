"""sales_register_vs_gl and purchase_register_vs_gl (docs/diligence.md D4).

The register's invoice lines of the period (Amount in Rs, before GST; by invoice date, from every
loaded registers file) are compared with the Ledger Vouchers entries of the period on the ledgers
confirmed to the books account: revenue for sales (credit - debit), purchases of stock-in-trade
and cost of materials for purchases (debit - credit). Left is the register, right is the books;
difference = register - books. Three levels, in this order:

- month: register invoice month vs entry month; a roll-up, listed;
- party: register customer or vendor token vs the entry's particulars (the party ledger, by name:
  matched to the register's token through the masked names, `fin.diligence.mask.Parties`); a
  roll-up, listed;
- invoice: the line a query can be raised on. Pass 1 matches the register's invoice number with the
  voucher number; pass 2 pairs what is left of the same party and the same amount (the voucher
  number differs: the note says so). An invoice only in the register is left_only, a voucher only
  in the books is right_only.

A foreign-currency invoice on both sides whose amounts differ only by the ledger's conversion rate
(the books' rupees within RATE_BAND of the register's) is an fx line: the note gives both rates and
FX gain or loss. It is listed, never queried, never plugged. A larger difference stays a difference.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.diligence.engagement import rupees
from fin.diligence.mask import Parties
from fin.reconcile.base import (
    ZERO,
    Computed,
    books_entries,
    count_text,
    kind_of,
    loaded_file,
    make_line,
    month_of,
    require_file,
    rows_text,
    unconfirmed_blocks,
)

RATE_BAND = Decimal("0.10")  # an fx line: the ledger's rate is within 10% of the register's


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str  # register_lines.kind
    party_column: str
    accounts: tuple[str, ...]  # the ledgers' accounts, read as the books side
    credit_side: bool  # books amount = credit - debit (sales), else debit - credit
    what: str


SALES = Spec("sales_register_vs_gl", "sales", "customer", ("revenue",), True, "revenue")
PURCHASE = Spec(
    "purchase_register_vs_gl",
    "purchase",
    "vendor",
    ("purchases_stock_in_trade", "cost_of_materials"),
    False,
    "purchases",
)


@dataclass
class Doc:
    """One invoice (register) or voucher (books): lines with the same number added up."""

    key: str
    party: str | None
    day: date
    number: str = ""
    amount: Decimal = ZERO
    refs: list[tuple[str, str, int]] = field(default_factory=list)
    foreign: Decimal | None = None
    currency: str | None = None


def _register(
    con: duckdb.DuckDBPyConnection, spec: Spec, engagement_id: int, start: date, end: date
) -> tuple[list[Any], int]:
    rows = con.execute(
        f'SELECT f.file_id, f.file_name, r.sheet, r."row", r.invoice_no, r.invoice_date, '
        f"r.{spec.party_column}, r.amount_inr, r.amount, r.currency "
        "FROM register_lines r JOIN source_files f USING (file_id) WHERE f.engagement_id = ? "
        "AND f.kind = 'registers' AND f.status = 'loaded' AND r.kind = ? "
        'AND r.invoice_date BETWEEN ? AND ? ORDER BY f.file_id, r.sheet, r."row"',
        [engagement_id, spec.kind, start, end],
    ).fetchall()
    return rows, min((row[0] for row in rows), default=0)


def applies(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str, spec: Spec
) -> bool:
    """The period's Ledger Vouchers and registers covering it are loaded."""
    voucher = loaded_file(con, engagement["engagement_id"], "ledger_vouchers", period)
    return voucher is not None and bool(
        _register(con, spec, engagement["engagement_id"], voucher[2], voucher[3])[0]
    )


def _docs(register: list[Any], spec: Spec) -> list[Doc]:
    """Register invoices: lines with the same number (and, for purchases, the same vendor: vendors
    number their own invoices) added up. A number that two parties share is shown with the party."""
    docs: dict[tuple[str, str], Doc] = {}
    for _, file_name, sheet, row, number, day, party, amount_inr, amount, currency in register:
        number = str(number).strip()
        scope = party if spec.kind == "purchase" else ""
        doc = docs.setdefault((scope, number), Doc(number, party, day, number))
        doc.amount += amount_inr
        doc.refs.append((file_name, sheet, row))
        if currency and currency.upper() != "INR" and amount is not None:
            doc.currency = currency
            doc.foreign = (doc.foreign or ZERO) + Decimal(amount)
    counts: dict[str, int] = {}
    for doc in docs.values():
        counts[doc.number] = counts.get(doc.number, 0) + 1
    for doc in docs.values():
        if counts[doc.number] > 1:
            doc.key = f"{doc.number} ({doc.party})"
    return list(docs.values())


def _vouchers(entries: list[Any], file_name: str, spec: Spec, parties: Parties) -> dict[str, Doc]:
    """Books vouchers: lines with the same voucher number added up; the party is the particulars
    ledger as the registers know it (its token when it is a register customer or vendor)."""
    docs: dict[str, Doc] = {}
    for sheet, row, day, particulars, number, debit, credit in entries:
        key = str(number).strip() if number is not None else "(no number)"
        doc = docs.setdefault(key, Doc(key, parties.party(particulars), day))
        doc.amount += (credit - debit) if spec.credit_side else (debit - credit)
        doc.refs.append((file_name, sheet, row))
    return docs


def _pairs(
    register: list[Doc], books: dict[str, Doc], spec: Spec
) -> list[tuple[Doc | None, Doc | None, str | None]]:
    pairs: list[tuple[Doc | None, Doc | None, str | None]] = []
    used: set[str] = set()
    left: list[Doc] = []
    for doc in register:
        voucher = books.get(doc.number)
        if (
            voucher
            and (spec.kind == "sales" or voucher.party == doc.party)
            and doc.number not in used
        ):
            used.add(doc.number)
            pairs.append((doc, voucher, None))
        else:
            left.append(doc)
    free = sorted((d for k, d in books.items() if k not in used), key=lambda d: (d.day, d.key))
    for doc in sorted(left, key=lambda d: (d.day, d.key)):
        match = next((v for v in free if v.party == doc.party and v.amount == doc.amount), None)
        if match is None:
            pairs.append((doc, None, None))
            continue
        free.remove(match)
        note = f"matched on party and amount: the voucher number is {match.key}, not {doc.number}"
        pairs.append((doc, match, note))
    pairs += [(None, voucher, None) for voucher in free]
    return sorted(pairs, key=lambda p: ((p[0] or p[1]).day, (p[0] or p[1]).key))  # type: ignore[union-attr]


def _fx(spec: Spec, register: Doc, books: Doc) -> str | None:
    """The fx note when the two sides differ only by the conversion rate, else None."""
    if not register.foreign or not register.amount or books.amount == register.amount:
        return None
    ratio = books.amount / register.amount
    if abs(ratio - 1) > RATE_BAND or ratio < 0:
        return None
    gain = (books.amount > register.amount) == spec.credit_side
    return (
        f"{register.currency} {register.foreign:,.2f}: register rate "
        f"{register.amount / register.foreign:.4f}, ledger rate "
        f"{books.amount / register.foreign:.4f}; register {rupees(register.amount)}, books "
        f"{rupees(books.amount)}: FX {'gain' if gain else 'loss'} "
        f"{rupees(abs(books.amount - register.amount))}"
    )


def _invoice_line(spec: Spec, pair: tuple[Doc | None, Doc | None, str | None]) -> dict[str, Any]:
    register, books, note = pair
    left, right = (register.amount if register else None), (books.amount if books else None)
    kind = kind_of(left, right)
    if register and books and kind == "difference" and (fx := _fx(spec, register, books)):
        kind, note = "fx", fx
    doc = register or books
    assert doc is not None
    line = make_line(
        "invoice",
        doc.key,
        left,
        right,
        rows_text(register.refs) if register else None,
        rows_text(books.refs) if books else None,
        kind=kind,
        note=note,
    )
    line["party"] = doc.party  # the party line it rolls up to (`fin discrepancies`)
    return line


def _rollup(
    level: str,
    left: dict[str, list[Any]],
    right: dict[str, list[Any]],
    sources: tuple[str, str, str, str],
) -> list[dict[str, Any]]:
    lines = []
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        lines.append(
            make_line(
                level,
                key,
                a[0] if a else None,
                b[0] if b else None,
                count_text(sources[0], sources[1], a[1]) if a else None,
                count_text(sources[2], sources[3], b[1]) if b else None,
            )
        )
    return lines


def _totals(items: list[tuple[str, Decimal]]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for key, amount in items:
        total = out.setdefault(key, [ZERO, 0])
        total[0] += amount
        total[1] += 1
    return out


def compute(
    con: duckdb.DuckDBPyConnection,
    company: str,
    engagement: dict[str, Any],
    period: str,
    spec: Spec,
) -> Computed:
    """The reconciliation of the period. E840 when the Ledger Vouchers or registers are not loaded
    or no ledger is confirmed to the books account."""
    engagement_id = engagement["engagement_id"]
    voucher = require_file(
        con, company, engagement_id, "ledger_vouchers", period, "Ledger Vouchers"
    )
    rows, first_file = _register(con, spec, engagement_id, voucher[2], voucher[3])
    if not rows:
        raise ReconCannotRun(
            f"{company} {period}: no loaded {spec.kind} register has invoices dated "
            f"{voucher[2]} to {voucher[3]} (`fin ingest {company} <file> --kind registers`); "
            "nothing recorded"
        )
    entries = books_entries(con, company, period, voucher[0], spec.accounts, spec.what)
    parties = Parties(con)
    register, books = _docs(rows, spec), _vouchers(entries, voucher[1], spec, parties)
    sources = (rows[0][1], rows[0][2], voucher[1], entries[0][0] if entries else "")
    by_month = _rollup(
        "month",
        _totals([(month_of(r[5]), r[7]) for r in rows]),
        _totals(
            [
                (month_of(e[2]), (e[6] - e[5]) if spec.credit_side else (e[5] - e[6]))
                for e in entries
            ]
        ),
        sources,
    )
    by_party = _rollup(
        "party",
        _totals([(r[6], r[7]) for r in rows]),
        _totals(
            [
                (parties.party(e[3]) or "", (e[6] - e[5]) if spec.credit_side else (e[5] - e[6]))
                for e in entries
            ]
        ),
        sources,
    )
    invoices = [_invoice_line(spec, pair) for pair in _pairs(register, books, spec)]
    return Computed(
        [*by_month, *by_party, *invoices],
        first_file,
        voucher[0],
        unconfirmed_blocks(con, voucher[0]),
    )


def sales_register_vs_gl(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """Sales register vs the revenue ledgers' Ledger Vouchers entries, for the period."""
    return compute(con, company, engagement, period, SALES)


def purchase_register_vs_gl(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """Purchase register vs the purchase ledgers' Ledger Vouchers entries, for the period."""
    return compute(con, company, engagement, period, PURCHASE)
