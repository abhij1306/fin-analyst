"""purchase_register_vs_gstr2b (SPEC-20): the purchase register against GSTR-2B, invoice by invoice.

Left is GSTR-2B (what the suppliers reported, `gstr2b_lines`), right is the purchase register's
lines dated in the 2B file's period (Amount in Rs, before GST); both are taxable values and the
difference is 2B - register. Lines with the same vendor and document number are added up. Pass 1
matches by vendor token and invoice number; pass 2 matches what is left by invoice number and
taxable value in the same month, shown as matched with "vendor names differ" and both tokens, so a
supplier whose 2B name is not the register's is not reported missing on both sides (fin does not
guess an alias otherwise). An invoice in the register and not in 2B (the vendor has not reported
it: the company's input tax credit is at risk) is right_only, one in 2B and not in the register is
left_only, a different taxable value is a difference: at or above the threshold each is for a query
(`fin query raise --items`). Level 'invoice', key '<month> <vendor token> <invoice number>'.
A credit or debit note is its own line (its type in the key), never added to an invoice with the
same number. Unlike the four GST reconciliations it is not a gate input (`fin.reconcile.NOT_GATED`).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.extract.profiles import norm
from fin.reconcile.base import ZERO, Computed, loaded_file, make_line, require_file, rows_text

NAME = "purchase_register_vs_gstr2b"
LEVEL = "invoice"
NOTES = ("credit note", "debit note")


@dataclass
class Doc:
    vendor: str
    number: str
    day: date
    note: str | None = None  # a credit or debit note's type (never summed with an invoice)
    amount: Decimal = ZERO
    refs: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def match(self) -> tuple[str, str, str | None]:
        return self.vendor, norm(self.number), self.note


def _docs(rows: list[Any]) -> list[Doc]:
    docs: dict[tuple[str, str, str | None], Doc] = {}
    for file_name, sheet, row, vendor, number, day, value, kind in rows:
        note = kind if kind and norm(kind) in NOTES else None
        doc = Doc(vendor, str(number).strip(), day, note)
        doc = docs.setdefault(doc.match, doc)
        doc.amount += Decimal(value)
        doc.refs.append((file_name, sheet, row))
    return list(docs.values())


def _two_b(con: duckdb.DuckDBPyConnection, file_id: int) -> list[Any]:
    return con.execute(
        'SELECT f.file_name, g.sheet, g."row", g.vendor, g.invoice_no, g.invoice_date, '
        "g.taxable_value, g.document_type FROM gstr2b_lines g JOIN source_files f USING (file_id) "
        'WHERE g.file_id = ? ORDER BY g.sheet, g."row"',
        [file_id],
    ).fetchall()


def _register(
    con: duckdb.DuckDBPyConnection, engagement_id: int, start: date, end: date
) -> list[Any]:
    return con.execute(
        'SELECT f.file_name, r.sheet, r."row", r.vendor, r.invoice_no, r.invoice_date, '
        "r.amount_inr, f.file_id FROM register_lines r JOIN source_files f USING (file_id) "
        "WHERE f.engagement_id = ? AND f.kind = 'registers' AND f.status = 'loaded' "
        "AND r.kind = 'purchase' AND r.invoice_date BETWEEN ? AND ? "
        'ORDER BY f.file_id, r.sheet, r."row"',
        [engagement_id, start, end],
    ).fetchall()


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """The period's GSTR-2B and purchase register lines in its dates are loaded."""
    found = loaded_file(con, engagement["engagement_id"], "gstr2b", period)
    return found is not None and bool(
        _register(con, engagement["engagement_id"], found[2], found[3])
    )


def _month(day: date) -> str:
    return f"{day:%Y-%m}"


def _pairs(left: list[Doc], right: list[Doc]) -> list[tuple[Doc | None, Doc | None, str | None]]:
    by_key = {doc.match: doc for doc in right}
    pairs: list[tuple[Doc | None, Doc | None, str | None]] = []
    rest: list[Doc] = []
    for doc in left:
        other = by_key.pop(doc.match, None)
        if other is None:
            rest.append(doc)
        else:
            pairs.append((doc, other, None))
    free = list(by_key.values())
    for doc in rest:
        found = next(
            (
                r
                for r in free
                if norm(r.number) == norm(doc.number)
                and r.note == doc.note
                and r.amount == doc.amount
                and _month(r.day) == _month(doc.day)
            ),
            None,
        )
        if found is None:
            pairs.append((doc, None, None))
            continue
        free.remove(found)
        note = f"vendor names differ: GSTR-2B {doc.vendor}, register {found.vendor}"
        pairs.append((doc, found, note))
    pairs += [(None, doc, None) for doc in free]
    return sorted(pairs, key=lambda p: _key(p[0] or p[1]))  # type: ignore[arg-type]


def _key(doc: Doc) -> str:
    return f"{_month(doc.day)} {doc.vendor} {doc.number}" + (f" ({doc.note})" if doc.note else "")


def compute(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """The period's comparison. E840 when its GSTR-2B or the purchase register lines in its dates
    are not loaded."""
    eid = engagement["engagement_id"]
    found = require_file(con, company, eid, "gstr2b", period, "GSTR-2B")
    register = _register(con, eid, found[2], found[3])
    if not register:
        raise ReconCannotRun(
            f"{company} {period}: no loaded purchase register line dated {found[2]} to "
            f"{found[3]} (`fin ingest {company} <file> --kind registers`); nothing recorded"
        )
    pairs = _pairs(_docs(_two_b(con, found[0])), _docs([(*row[:7], None) for row in register]))
    lines = []
    for two_b, books, note in pairs:
        doc = books or two_b
        if doc is None:  # pragma: no cover - a pair always has a side
            continue
        line = make_line(
            LEVEL,
            _key(doc),
            two_b.amount if two_b else None,
            books.amount if books else None,
            rows_text(two_b.refs) if two_b else None,
            rows_text(books.refs) if books else None,
            note=note,
        )
        line["party"] = doc.vendor
        lines.append(line)
    return Computed(lines, found[0], min(row[7] for row in register))
