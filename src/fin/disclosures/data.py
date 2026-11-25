"""What the FDD report (F8) reads for F5's sections: contingencies and commitments, and related
parties. `section_data(con, engagement_id)` returns plain rows; empty lists when nothing is loaded
(the report says so). Every Claude-read row keeps read_by and its file + page."""

from typing import Any

import duckdb

from fin.db.rows import fetch_dicts
from fin.disclosures.load import read
from fin.disclosures.reading import KINDS, RELATED
from fin.disclosures.rpt import NAME


def _view(con: duckdb.DuckDBPyConnection, view: str, engagement_id: int) -> list[dict[str, Any]]:
    return fetch_dicts(
        con, f"SELECT * FROM {view} WHERE engagement_id = ? ORDER BY ALL", [engagement_id]
    )


def section_data(con: duckdb.DuckDBPyConnection, engagement_id: int) -> dict[str, Any]:
    """schedules: {kind: rows} for every kind (contingencies, commitments, guarantees, litigation,
    tax matters, off-balance-sheet items, related parties and their transactions and balances);
    matches: what each related party is in the books; transactions, pricing, loans: the views;
    rpt_vs_books: the latest run's lines per period."""
    rows = read(con, engagement_id)
    runs = fetch_dicts(
        con,
        "SELECT i.*, r.period, r.recon_id FROM recon_items i JOIN recon_runs r USING (recon_id) "
        "WHERE r.recon_id IN (SELECT max(recon_id) FROM recon_runs WHERE engagement_id = ? "
        "AND name = ? GROUP BY period) ORDER BY r.period, i.item_no",
        [engagement_id, NAME],
    )
    return {
        "schedules": {kind: [r for r in rows if r["kind"] == kind] for kind in KINDS},
        "related_kinds": list(RELATED),
        "matches": _view(con, "related_party_matches", engagement_id),
        "transactions": _view(con, "related_party_transactions", engagement_id),
        "pricing": _view(con, "related_party_pricing", engagement_id),
        "loans": _view(con, "related_party_loans", engagement_id),
        "rpt_vs_books": runs,
    }
