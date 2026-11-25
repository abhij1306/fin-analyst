"""`fin query raise --items 12,13,14`: one accountant query (an issue) for many recon lines.

Claude groups the lines at or above the threshold of one period into an issue and drafts its
wording; the user approves it like any query. The query's amount is the lines' differences added
up without their signs; its sources are the lines' two sources; `query_items` links the lines, so
the count and value of each kind of difference come from it. The lines must be in the latest run of
their reconciliation, on data that has not changed since (E841), and be lines that need a query and
are in no active query (a withdrawn one leaves its lines open) (E842).
"""

from typing import Any

import duckdb

from fin.audit.errors import ReconItemNotQueryable, ReconItemUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.diligence.queries import draft_in
from fin.reconcile import current_hash

SHOWN = 10  # lines whose sources a query names; the rest are counted


def parse_ids(text: str) -> list[int]:
    """'12,13, 14' -> [12, 13, 14]. ValueError for anything else."""
    ids = [int(part.strip()) for part in text.split(",") if part.strip()]
    if not ids:
        raise ValueError("no item ids")
    return list(dict.fromkeys(ids))


def _lines(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], ids: list[int]
) -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in ids)
    found = {
        row["item_id"]: row
        for row in fetch_dicts(
            con,
            "SELECT i.*, r.engagement_id, r.name, r.period, r.input_hash, "
            "(SELECT status FROM accountant_queries q WHERE q.query_id = i.query_id) AS "
            "query_status, r.recon_id = (SELECT max(recon_id) "
            "FROM recon_runs WHERE engagement_id = r.engagement_id AND name = r.name "
            f"AND period = r.period) AS is_latest FROM recon_items i JOIN recon_runs r "
            f"USING (recon_id) WHERE i.item_id IN ({marks})",
            ids,
        )
    }
    unknown = [
        i for i in ids if i not in found or found[i]["engagement_id"] != engagement["engagement_id"]
    ]
    if unknown:
        raise ReconItemUnknown(
            f"reconciliation lines {', '.join(f'#{i}' for i in unknown)} are not lines of the "
            f"open engagement; nothing raised. Take the ids from the latest `fin reconcile`"
        )
    stale = [i for i in ids if not found[i]["is_latest"]]
    if stale:
        raise ReconItemUnknown(
            f"reconciliation lines {', '.join(f'#{i}' for i in stale)} are not in the latest run "
            "of their reconciliation (it was run again since); nothing raised. Take the ids from "
            "the latest `fin reconcile`"
        )
    changed = sorted(
        {
            (line["name"], line["period"])
            for line in found.values()
            if current_hash(con, company, engagement, line["name"], line["period"])
            != line["input_hash"]
        }
    )
    if changed:
        raise ReconItemUnknown(
            "the data of "
            + ", ".join(f"{name} {period}" for name, period in changed)
            + " has changed since it was reconciled; nothing raised. Run `fin reconcile "
            f"{company}` again and take the ids from it"
        )
    return [found[i] for i in ids]


def summary(con: duckdb.DuckDBPyConnection, query_id: int) -> str:
    """'3 left_only ₹4,00,000.00; 1 difference ₹50,000.00': count and value per kind."""
    rows = con.execute(
        "SELECT i.kind, count(*), sum(abs(i.difference)) FROM query_items q JOIN recon_items i "
        "USING (item_id) WHERE q.query_id = ? GROUP BY i.kind ORDER BY i.kind",
        [query_id],
    ).fetchall()
    return "; ".join(f"{count} {kind} {rupees(value)}" for kind, count, value in rows)


def raise_from_items(
    run: Run, company: str, ids: list[int], question: str
) -> tuple[dict[str, Any], str]:
    """A draft query (origin 'recon') for the lines, and its count and value per kind. E841 for an
    unknown or stale line, E842 for a line that cannot be raised or lines of several periods."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        lines = _lines(con, company, engagement, ids)
        bad = [
            f"#{line['item_id']}"
            for line in lines
            if not line["needs_query"]
            or (line["query_id"] is not None and line["query_status"] != "withdrawn")
        ]
        if bad:
            raise ReconItemNotQueryable(
                f"lines {', '.join(bad)} cannot be raised: only a difference at or above the "
                "threshold that is not in a query yet can be (matched, fx, not reconciled, "
                "unmapped, roll-up and below-threshold lines cannot); nothing raised"
            )
        periods = sorted({line["period"] for line in lines})
        if len(periods) > 1:
            raise ReconItemNotQueryable(
                f"the lines are of several periods ({', '.join(periods)}); raise one query per "
                "period. Nothing raised"
            )
        sources = [
            part
            for line in lines[:SHOWN]
            for part in (line["left_source"], line["right_source"])
            if part
        ]
        more = f"; and {len(lines) - SHOWN} more lines" if len(lines) > SHOWN else ""
        query = draft_in(
            run,
            company,
            con,
            engagement,
            {
                "origin": "recon",
                "finding_id": None,
                "period": periods[0],
                "scope": "standalone",
                "question": question,
                "amount": sum(abs(line["difference"]) for line in lines),
                "sources": "; ".join(dict.fromkeys(sources)) + more,
            },
        )
        for line in lines:
            con.execute(
                "UPDATE recon_items SET query_id = ? WHERE item_id = ?",
                [query["query_id"], line["item_id"]],
            )
            con.execute(
                "INSERT INTO query_items (query_id, item_id) VALUES (?, ?)",
                [query["query_id"], line["item_id"]],
            )
        return query, summary(con, query["query_id"])
