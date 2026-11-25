"""rpt_vs_books (docs/fdd.md F5): the related party transactions the notes disclose, against the
books.

For the period, each related_party_transaction row of the loaded readings that says what it compares
with (`books`) is added up per party, books and ledger (left, disclosed) and compared with (right):

- sales: the sales register lines of the customers linked to the party (`related_party_matches`),
  Amount in Rs before GST, by the line's period;
- purchases: the purchase register lines of its vendors;
- debit, credit: the debits or credits on the row's ledger in the period's loaded Ledger Vouchers.

difference = disclosed - books. A line whose books side cannot be read (no register lines or Ledger
Vouchers for the period, or no customer or vendor linked to the party yet) is not_reconciled, listed
with the reason, never queried. The rest are related_party lines: at or above the threshold Claude
groups them into queries (`fin query raise --items`). Nothing is changed to make it tie.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile.base import Computed, loaded_file, make_line

NAME = "rpt_vs_books"
LEVEL = "related_party"
_ROWS = """
SELECT d.counterparty, d.books, d.ledger, sum(d.amount), min(d.file_id),
    string_agg(DISTINCT f.file_name || ' p.' || d.page, '; '
        ORDER BY f.file_name || ' p.' || d.page)
FROM disclosures d JOIN disclosure_files f USING (file_id)
WHERE d.engagement_id = ? AND d.period = ? AND d.kind = 'related_party_transaction'
    AND d.books IS NOT NULL
GROUP BY ALL ORDER BY 1, 2, 3
"""
_REGISTER = """
SELECT sum(r.amount_inr), count(*), string_agg(DISTINCT f.file_name, '; ')
FROM register_lines r JOIN source_files f USING (file_id)
JOIN related_party_matches m ON m.engagement_id = f.engagement_id AND m.party = ?
    AND m.link_kind = ? AND m.target = (CASE r.kind WHEN 'sales' THEN r.customer ELSE r.vendor END)
WHERE f.engagement_id = ? AND f.status = 'loaded' AND f.kind = 'registers' AND r.kind = ?
    AND r.period = ?
"""
REGISTER = {"sales": ("sales", "customer"), "purchases": ("purchase", "vendor")}


def _rows(con: duckdb.DuckDBPyConnection, engagement_id: int, period: str) -> list[Any]:
    return con.execute(_ROWS, [engagement_id, period]).fetchall()


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """A loaded reading has a related party transaction of the period to compare."""
    return bool(_rows(con, engagement["engagement_id"], period))


def _register_side(
    con: duckdb.DuckDBPyConnection, engagement_id: int, period: str, party: str, books: str
) -> tuple[Decimal | None, str | None, str | None]:
    """(books amount, source, reason not reconciled)."""
    kind, link_kind = REGISTER[books]
    any_line = con.execute(
        "SELECT count(*) FROM register_lines r JOIN source_files f USING (file_id) "
        "WHERE f.engagement_id = ? AND f.status = 'loaded' AND r.kind = ? AND r.period = ?",
        [engagement_id, kind, period],
    ).fetchone()
    if not any_line or not any_line[0]:
        return None, None, f"no {kind} register lines are loaded for {period}"
    linked = con.execute(
        "SELECT count(*) FROM related_party_matches WHERE engagement_id = ? AND party = ? "
        "AND link_kind = ?",
        [engagement_id, party, link_kind],
    ).fetchone()
    if not linked or not linked[0]:
        return (
            None,
            None,
            (
                f"no {link_kind} is linked to {party} (`fin disclosures related`, then the user "
                "confirms with `fin review confirm --related`)"
            ),
        )
    row = con.execute(_REGISTER, [party, link_kind, engagement_id, kind, period]).fetchone()
    assert row is not None
    if not row[1]:
        return None, None, None
    return row[0], f"{row[2]} {kind} register ({row[1]} lines)", None


def _ledger_side(
    con: duckdb.DuckDBPyConnection,
    voucher: tuple[int, str, Any, Any] | None,
    books: str,
    ledger: str,
) -> tuple[Decimal | None, str | None]:
    assert voucher is not None
    column = "debit" if books == "debit" else "credit"
    row = con.execute(
        f"SELECT sum({column}), count(*) FROM ledger_entries WHERE file_id = ? AND ledger = ?",
        [voucher[0], ledger],
    ).fetchone()
    assert row is not None
    if not row[1]:
        return None, None
    return row[0], f"{voucher[1]} {ledger} {column}s ({row[1]} lines)"


def rpt_vs_books(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """The period's comparison. E840 when no loaded reading has a transaction to compare."""
    engagement_id = engagement["engagement_id"]
    rows = _rows(con, engagement_id, period)
    if not rows:
        raise ReconCannotRun(
            f"{company} {period}: no related party transaction with books in a loaded disclosure "
            f"reading (`fin disclosures load {company} <file> --reading <csv>`); nothing recorded"
        )
    voucher = loaded_file(con, engagement_id, "ledger_vouchers", period)
    lines = []
    for party, books, ledger, disclosed, _, pages in rows:
        key = f"{party} {books}" + (f" {ledger}" if ledger else "")
        reason = None
        if books in REGISTER:
            amount, source, reason = _register_side(con, engagement_id, period, party, books)
        elif voucher is None:
            amount, source = None, None
            reason = f"no Ledger Vouchers are loaded for {period}"
        else:
            amount, source = _ledger_side(con, voucher, books, ledger)
        if reason:
            line = make_line(LEVEL, key, disclosed, None, pages, None, kind="not_reconciled")
            line["note"] = reason
        else:
            line = make_line(LEVEL, key, disclosed, amount, pages, source)
        lines.append(line)
    return Computed(lines, min(row[4] for row in rows), voucher[0] if voucher else None)
