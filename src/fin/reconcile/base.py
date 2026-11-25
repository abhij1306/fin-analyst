"""What the D4 reconciliations share (docs/diligence.md D4): a line, the books side, saving a run.

Each reconciliation is a function `compute(con, engagement, period) -> Computed`: deterministic SQL
and grouping over the loaded source rows, nothing written. `store.save` writes the run and its
lines; gate G2 (`fin.reconcile.checks`) computes again to tell whether the data changed since.
A line is a dict with the columns of `recon_items` but recon_id, item_no, item_id, needs_query
and query_id. Differences are queried, never fixed: nothing here changes company data.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun

ZERO = Decimal("0.00")
QUERIED_KINDS = ("difference", "left_only", "right_only", "undated")
DETAIL_LEVELS = (
    "invoice",
    "ledger",
    "payroll",
    "match",
    "receipt",
    "balance",
    "order",
    "gst",
    "related_party",  # rpt_vs_books (fin.disclosures.rpt)
)  # lines a query can be raised on
SHOWN_ROWS = 5  # source rows named in a line's source; the rest are counted


@dataclass(frozen=True)
class Computed:
    lines: list[dict[str, Any]]
    left_file_id: int
    right_file_id: int | None
    unmapped: int = 0  # ledgers of the Ledger Vouchers not confirmed to an account
    as_of: date | None = None  # bank_vs_invoices: the period's last day, open invoices age to it
    due_days: int | None = None  # bank_vs_invoices: the invoice due days used


def kind_of(left: Decimal | None, right: Decimal | None) -> str:
    difference = (left or ZERO) - (right or ZERO)
    if difference == 0:
        return "matched"
    return {(True, True): "difference", (True, False): "left_only"}.get(
        (left is not None, right is not None), "right_only"
    )


def make_line(
    level: str,
    key: str,
    left: Decimal | None,
    right: Decimal | None,
    left_source: str | None,
    right_source: str | None,
    *,
    kind: str | None = None,
    account: str | None = None,
    ledger: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """A reconciliation line; difference = left - right (a missing side counts as 0)."""
    return {
        "kind": kind or kind_of(left, right),
        "level": level,
        "key": key,
        "note": note,
        "account": account,
        "ledger": ledger,
        "left_amount": left,
        "right_amount": right,
        "difference": (left or ZERO) - (right or ZERO),
        "left_source": left_source,
        "right_source": right_source,
    }


def needs_query(line: dict[str, Any], threshold: Decimal) -> bool:
    """A difference, left_only or right_only line at a detail level at or above the threshold."""
    return (
        line["level"] in DETAIL_LEVELS
        and line["kind"] in QUERIED_KINDS
        and abs(line["difference"]) >= threshold
    )


def rows_text(refs: list[tuple[str, str, int]]) -> str | None:
    """'file sheet!2, 3 and 4 more' for (file name, sheet, row) references, per file and sheet."""
    groups: dict[tuple[str, str], list[int]] = {}
    for file_name, sheet, row in refs:
        groups.setdefault((file_name, sheet), []).append(row)
    parts = []
    for (file_name, sheet), rows in groups.items():
        more = f" and {len(rows) - SHOWN_ROWS} more" if len(rows) > SHOWN_ROWS else ""
        shown = ", ".join(str(row) for row in rows[:SHOWN_ROWS])
        parts.append(f"{file_name} {sheet}!{shown}{more}")
    return "; ".join(parts) or None


def count_text(file_name: str, sheet: str, count: int) -> str:
    return f"{file_name} {sheet} ({count} lines)"


def month_of(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def input_hash(computed: Computed, threshold: Decimal) -> str:
    """sha256 of what a reconciliation compared: its lines, files and the threshold."""
    value = [computed.lines, computed.left_file_id, computed.right_file_id, str(threshold)]
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def loaded_file(
    con: duckdb.DuckDBPyConnection, engagement_id: int, kind: str, period: str
) -> tuple[int, str, date, date] | None:
    """(file_id, file_name, period_start, period_end) of the period's loaded file of that kind."""
    row = con.execute(
        "SELECT file_id, file_name, period_start, period_end FROM source_files "
        "WHERE engagement_id = ? AND kind = ? AND period = ? AND status = 'loaded' "
        "ORDER BY file_id DESC LIMIT 1",
        [engagement_id, kind, period],
    ).fetchone()
    return None if row is None else (row[0], row[1], row[2], row[3])


def require_file(
    con: duckdb.DuckDBPyConnection,
    company: str,
    engagement_id: int,
    kind: str,
    period: str,
    what: str,
) -> tuple[int, str, date, date]:
    found = loaded_file(con, engagement_id, kind, period)
    if found is None:
        raise ReconCannotRun(
            f"{company} {period}: no loaded {what} (`fin ingest {company} <file> --kind {kind}`); "
            "nothing recorded"
        )
    return found


def confirmed_ledgers(con: duckdb.DuckDBPyConnection, accounts: tuple[str, ...]) -> list[str]:
    """The ledgers the user confirmed to one of the accounts."""
    marks = ", ".join("?" for _ in accounts)
    return [
        ledger
        for (ledger,) in con.execute(
            f"SELECT ledger FROM ledger_accounts WHERE status = 'confirmed' "
            f"AND account IN ({marks}) ORDER BY ledger",
            list(accounts),
        ).fetchall()
    ]


def unconfirmed_blocks(con: duckdb.DuckDBPyConnection, voucher_file_id: int) -> int:
    """Ledgers of the Ledger Vouchers not confirmed to an account (their side may be incomplete)."""
    row = con.execute(
        "SELECT count(DISTINCT ledger) FROM ledger_blocks WHERE file_id = ? AND ledger NOT IN "
        "(SELECT ledger FROM ledger_accounts WHERE status = 'confirmed')",
        [voucher_file_id],
    ).fetchone()
    assert row is not None
    return int(row[0])


def books_entries(
    con: duckdb.DuckDBPyConnection,
    company: str,
    period: str,
    voucher_file_id: int,
    accounts: tuple[str, ...],
    what: str,
) -> list[Any]:
    """The Ledger Vouchers entries (sheet, row, date, particulars, voucher no, debit, credit) of the
    ledgers confirmed to the accounts. E840 when no ledger is."""
    ledgers = confirmed_ledgers(con, accounts)
    if not ledgers:
        raise ReconCannotRun(
            f"{company} {period}: no ledger is confirmed to {what} ({', '.join(accounts)}); map "
            f"the ledgers first (`fin map ledgers {company}`, then the user confirms); nothing "
            "recorded"
        )
    marks = ", ".join("?" for _ in ledgers)
    return con.execute(
        f'SELECT sheet, "row", entry_date, particulars, voucher_no, debit, credit '
        f'FROM ledger_entries WHERE file_id = ? AND ledger IN ({marks}) ORDER BY sheet, "row"',
        [voucher_file_id, *ledgers],
    ).fetchall()
