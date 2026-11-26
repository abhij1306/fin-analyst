"""FDD F4 (docs/fdd.md): debt-like items not in a trial balance ledger.

An indicative gratuity shortfall (F3), a probable contingency or a cut-off item is proposed as an
item (`fin netdebt item propose`, table `netdebt_items` in db/schema/fdd_netdebt.sql) with a
period of the open engagement (a year or its stub), a signed amount in rupees (positive raises net
debt), a description and evidence (a reference: a query, a fin output, file + page). The user
accepts or rejects it, as for adjustments:

    proposed -> accepted, or proposed -> rejected (with the reason).

Only accepted items count in `net_debt`; proposed ones are shown, not applied. Every change is a
run event.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import NetDebtItemInvalid, NetDebtItemUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees


def propose(
    run: Run, company: str, period: str, amount: Decimal, description: str, evidence: str
) -> dict[str, Any]:
    """Store a proposed item in the open engagement. E803 without one; E932 for a period not in it,
    an amount of 0, or an empty description or evidence."""
    if amount == 0:
        raise NetDebtItemInvalid("the amount is 0; nothing stored")
    for name, text in (("description", description), ("evidence", evidence)):
        if not text.strip():
            raise NetDebtItemInvalid(f"give the {name}; nothing stored")
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        known = engagement["periods"].split(",") + [engagement["stub"]] * bool(engagement["stub"])
        if period not in known:
            raise NetDebtItemInvalid(
                f"{period} is not a period of {company}'s open engagement ({', '.join(known)}); "
                "nothing stored"
            )
        now = utcnow()
        (item_id,) = con.execute(
            "INSERT INTO netdebt_items (engagement_id, period, amount, description, evidence, "
            "status, created_run_id, created_at, status_run_id, status_at) "
            "VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?) RETURNING item_id",
            [
                engagement["engagement_id"], period, amount,
                " ".join(description.split()), " ".join(evidence.split()),
                run.run_id, now, run.run_id, now,
            ],
        ).fetchone()  # fmt: skip
        _log(run, company, item_id, f"proposed {period} {amount}")
        return _items(con, engagement["engagement_id"], [item_id])[0]


def decide(
    run: Run, company: str, ids: list[int], status: str, reason: str | None = None
) -> list[dict[str, Any]]:
    """The user's decision on proposed items, all or nothing: 'accepted' or 'rejected' (with the
    reason). E933 for an id not in the open engagement, one not proposed, or a reject without a
    reason."""
    ids = list(dict.fromkeys(ids))
    if status == "rejected" and not (reason or "").strip():
        raise NetDebtItemUnknown("give the reason for rejecting with --reason; nothing changed")
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        if reason is not None:
            reason = " ".join(reason.split())
        rows = {r["item_id"]: r for r in _items(con, engagement_id)}
        for item_id in ids:
            row = rows.get(item_id)
            if row is None or row["status"] != "proposed":
                state = "not in the open engagement" if row is None else row["status"]
                raise NetDebtItemUnknown(
                    f"net debt item #{item_id} cannot be {status} ({state}); nothing changed "
                    f"(`fin netdebt item list {company}`)"
                )
        for item_id in ids:
            con.execute(
                "UPDATE netdebt_items SET status = ?, reason = ?, status_run_id = ?, "
                "status_at = ? WHERE item_id = ?",
                [status, reason, run.run_id, utcnow(), item_id],
            )
            _log(run, company, item_id, status + (f": {reason}" if reason else ""))
        return _items(con, engagement_id, ids)


def _log(run: Run, company: str, item_id: int, what: str) -> None:
    run.event(
        "netdebt",
        f"net debt item #{item_id}: {what}",
        context={"company": company, "item_id": item_id},
    )


def _items(
    con: duckdb.DuckDBPyConnection, engagement_id: int, ids: list[int] | None = None
) -> list[dict[str, Any]]:
    where = " AND list_contains(?::INTEGER[], item_id)" if ids is not None else ""
    return fetch_dicts(
        con,
        f"SELECT * FROM netdebt_items WHERE engagement_id = ?{where} ORDER BY item_id",
        [engagement_id] + ([ids] if ids is not None else []),
    )


def list_items(run: Run, company: str, status: str | None = None) -> list[dict[str, Any]]:
    """The open engagement's items (only those with `status`). E803 without one."""
    with company_database(run, company) as con:
        rows = _items(con, require_open(con, company)["engagement_id"])
    return [r for r in rows if status is None or r["status"] == status]


def format_item(row: dict[str, Any]) -> str:
    line = (
        f"net debt item #{row['item_id']} [{row['status']}] {row['period']} "
        f"{rupees(row['amount'])}: {row['description']} (evidence: {row['evidence']})"
    )
    return line + (f"; rejected: {row['reason']}" if row["reason"] else "")
