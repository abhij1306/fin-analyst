"""Saving a D4 reconciliation run (`recon_runs`, `recon_items`) and finding its lines again.

A run is one row per name and period, with the hash of what it compared (`base.input_hash`) so gate
G2 can tell when it must run again. fin drafts no query for these lines: lines at or above the
threshold are marked `needs_query`, Claude groups them into issues (`fin query raise --items`,
`fin.reconcile.items`) and the user approves them. A line that was in a query in an earlier run of
the same name and period, with the same level, key, kind and amounts (and the query not
withdrawn), is in that query again; a changed line is not, so it needs a new one.
"""

from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.db.conn import utcnow
from fin.reconcile.base import Computed, input_hash, needs_query

COLUMNS = (
    "kind", "level", "key", "note", "account", "ledger", "left_amount", "right_amount",
    "difference", "left_source", "right_source",
)  # fmt: skip
EXTRA = ("match_pass", "currency", "amount_inr", "party")  # NULL where a reconciliation has none


def latest_run(
    con: duckdb.DuckDBPyConnection, engagement_id: int, name: str, period: str
) -> tuple[int, str] | None:
    """(recon_id, input_hash) of the latest run of the name for the period."""
    row = con.execute(
        "SELECT recon_id, input_hash FROM recon_runs WHERE engagement_id = ? AND name = ? "
        "AND period = ? ORDER BY recon_id DESC LIMIT 1",
        [engagement_id, name, period],
    ).fetchone()
    return None if row is None else (row[0], row[1])


def open_lines(con: duckdb.DuckDBPyConnection, recon_id: int) -> int:
    """Lines of the run that need a query and are in none (or only in a withdrawn one)."""
    row = con.execute(
        "SELECT count(*) FROM recon_items WHERE recon_id = ? AND needs_query AND (query_id IS NULL "
        "OR query_id IN (SELECT query_id FROM accountant_queries WHERE status = 'withdrawn'))",
        [recon_id],
    ).fetchone()
    assert row is not None
    return int(row[0])


def query_rows(con: duckdb.DuckDBPyConnection, recon_id: int | None) -> list[list[Any]]:
    """What gates G2 and G3 hash of a run: its lines that need a query, with their query and
    whether it is withdrawn. Empty without a run."""
    if recon_id is None:
        return []
    return [
        list(row)
        for row in con.execute(
            "SELECT i.item_id, i.kind, i.difference, i.query_id, "
            "(SELECT status = 'withdrawn' FROM accountant_queries q "
            "WHERE q.query_id = i.query_id) FROM recon_items i "
            "WHERE i.recon_id = ? AND i.needs_query ORDER BY i.item_id",
            [recon_id],
        ).fetchall()
    ]


def _earlier_query(
    con: duckdb.DuckDBPyConnection, engagement_id: int, name: str, period: str, line: dict[str, Any]
) -> int | None:
    row = con.execute(
        "SELECT i.query_id FROM recon_items i JOIN recon_runs r USING (recon_id) "
        "JOIN accountant_queries q ON q.query_id = i.query_id AND q.status <> 'withdrawn' "
        "WHERE r.engagement_id = ? AND r.name = ? AND r.period = ? AND i.level = ? AND i.key = ? "
        "AND i.kind = ? AND i.left_amount IS NOT DISTINCT FROM ? "
        "AND i.right_amount IS NOT DISTINCT FROM ? AND i.amount_inr IS NOT DISTINCT FROM ? "
        "AND i.query_id IS NOT NULL "
        "ORDER BY r.recon_id DESC LIMIT 1",
        [
            engagement_id, name, period, line["level"], line["key"], line["kind"],
            line["left_amount"], line["right_amount"], line.get("amount_inr"),
        ],
    ).fetchone()  # fmt: skip
    return row[0] if row else None


def save(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    name: str,
    period: str,
    computed: Computed,
) -> dict[str, Any]:
    """Record the run and its lines (in the caller's transaction). Returns the result: recon_id,
    name, period, threshold, the lines (with item_id, needs_query, query_id) and the unmapped
    count."""
    engagement_id, threshold = engagement["engagement_id"], engagement["query_threshold"]
    new = con.execute(
        "INSERT INTO recon_runs (engagement_id, name, period, scope, left_file_id, right_file_id, "
        "threshold, input_hash, unmapped, as_of, due_days, run_id, created_at) "
        "VALUES (?, ?, ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING recon_id",
        [
            engagement_id, name, period, computed.left_file_id, computed.right_file_id,
            threshold, input_hash(computed, threshold), computed.unmapped, computed.as_of,
            computed.due_days, run.run_id, utcnow(),
        ],
    ).fetchone()  # fmt: skip
    assert new is not None
    lines = []
    for number, source in enumerate(computed.lines, start=1):
        line = dict(source)
        line["needs_query"] = line.get("needs_query", needs_query(line, threshold))
        line["query_id"] = (
            _earlier_query(con, engagement_id, name, period, line) if line["needs_query"] else None
        )
        columns = (*COLUMNS, *EXTRA)
        marks = ", ".join("?" for _ in columns)
        item = con.execute(
            f"INSERT INTO recon_items (recon_id, item_no, needs_query, query_id, "
            f"{', '.join(columns)}) VALUES (?, ?, ?, ?, {marks}) RETURNING item_id",
            [
                new[0],
                number,
                line["needs_query"],
                line["query_id"],
                *(line.get(c) for c in columns),
            ],
        ).fetchone()
        assert item is not None
        line["item_id"] = item[0]
        for m in line.get("match_lines", []):  # bank_vs_invoices: the lines behind the item
            con.execute(
                "INSERT INTO recon_match_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                [new[0], number, m["side"], *m["key"], m["party"], m["day"], m["reference"]]
                + [m["currency"], m["amount"], m["amount_inr"]],
            )
        if line["query_id"] is not None:
            con.execute(
                "INSERT INTO query_items (query_id, item_id) VALUES (?, ?)",
                [line["query_id"], item[0]],
            )
        lines.append(line)
    return {
        "recon_id": new[0],
        "name": name,
        "period": period,
        "threshold": threshold,
        "unmapped": computed.unmapped,
        "lines": lines,
    }
