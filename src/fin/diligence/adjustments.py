"""Adjustments (`fin adjust ...`): proposed effects on EBITDA, kept beside the reported figures.

Reported facts are never changed. An adjustment has a period of the engagement, a scope, a category
(config.toml [diligence] adjustment_categories), optionally the dictionary account it relates to, a
signed amount in rupees (its effect on EBITDA: positive raises it), a description, and evidence: a
query, a finding or a source reference (at least one). Claude proposes; the user accepts or rejects
(table `adjustments`, db/schema/diligence.sql):

    proposed -> accepted, or proposed -> rejected (with the reason).

Accepting or rejecting needs gate G2 approved (fin.diligence.gates): E801 otherwise.

Every adjustment proposed or decided is appended to the audit table `diligence_events`.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import AdjustmentInvalid, DiligenceChangeRefused
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence import gates
from fin.diligence.engagement import company_database, record_event, require_open, rupees
from fin.map.dictionary import load_accounts


def _get(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], company: str, adj_id: int
) -> dict[str, Any]:
    rows = fetch_dicts(
        con,
        "SELECT * FROM adjustments WHERE adj_id = ? AND engagement_id = ?",
        [adj_id, engagement["engagement_id"]],
    )
    if not rows:
        raise DiligenceChangeRefused(
            f"adjustment {adj_id} is not an adjustment of {company}'s open engagement "
            f"#{engagement['engagement_id']}; nothing changed. Adjustments: "
            f"`fin adjust list {company}`"
        )
    return rows[0]


def _check_links(
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    company: str,
    query_id: int | None,
    finding_id: int | None,
) -> None:
    """E805 when the query is not one of the engagement's, or the finding does not exist."""
    if query_id is not None:
        found = con.execute(
            "SELECT count(*) FROM accountant_queries WHERE query_id = ? AND engagement_id = ?",
            [query_id, engagement["engagement_id"]],
        ).fetchone()
        if found is None or found[0] == 0:
            raise DiligenceChangeRefused(
                f"query {query_id} is not a query of {company}'s open engagement; nothing "
                f"proposed. Queries: `fin query list {company}`"
            )
    if finding_id is not None:
        found = con.execute(
            "SELECT count(*) FROM findings WHERE finding_id = ?", [finding_id]
        ).fetchone()
        if found is None or found[0] == 0:
            raise DiligenceChangeRefused(
                f"finding {finding_id} of {company} does not exist; nothing proposed. Findings: "
                f"`fin findings list {company}`"
            )


def propose(
    run: Run,
    company: str,
    *,
    period: str,
    scope: str,
    category: str,
    amount: Decimal,
    description: str,
    account: str | None = None,
    query_id: int | None = None,
    finding_id: int | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Propose an adjustment. E804 without evidence, for a category not in config.toml, a period
    not in the engagement or an account not in the dictionary; E805 for an unknown query or
    finding."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        if query_id is None and finding_id is None and not source_ref:
            raise AdjustmentInvalid(
                "an adjustment needs evidence: --query, --finding or --source; nothing proposed"
            )
        categories = load_config(run.root).adjustment_categories
        if category not in categories:
            raise AdjustmentInvalid(
                f"category {category!r} is not in config.toml [diligence] adjustment_categories "
                f"({', '.join(categories)}); nothing proposed"
            )
        periods = engagement["periods"].split(",")
        if period not in periods:
            raise AdjustmentInvalid(
                f"period {period} is not in the engagement ({engagement['periods']}); nothing "
                "proposed"
            )
        if account is not None and account not in load_accounts(run.root):
            raise AdjustmentInvalid(
                f"account {account!r} is not in the dictionary (`fin map accounts`); nothing "
                "proposed"
            )
        _check_links(con, engagement, company, query_id, finding_id)
        now = utcnow()
        row = {
            "engagement_id": engagement["engagement_id"],
            "period": period,
            "scope": scope,
            "category": category,
            "account": account,
            "amount": amount,
            "description": description,
            "query_id": query_id,
            "finding_id": finding_id,
            "source_ref": source_ref or None,
            "status": "proposed",
            "created_run_id": run.run_id,
            "created_at": now,
            "status_run_id": run.run_id,
            "status_at": now,
        }
        new = con.execute(
            f"INSERT INTO adjustments ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)}) RETURNING adj_id",
            list(row.values()),
        ).fetchone()
        assert new is not None
        record_event(run, company, con, "adjustment", new[0])
        return _get(con, engagement, company, new[0])


def decide(
    run: Run, company: str, ids: list[int], status: str, reason: str | None = None
) -> list[dict[str, Any]]:
    """Accept or reject proposed adjustments, all or none. E801 unless gate G2 (and G1 before it)
    is approved; E805 for an unknown id or one that is not proposed."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        command = f"fin adjust {'accept' if status == 'accepted' else 'reject'}"
        gates.require(gates.context(con, run.root, company), "G2", command)
        for row in [_get(con, engagement, company, adj_id) for adj_id in ids]:
            if row["status"] != "proposed":
                raise DiligenceChangeRefused(
                    f"adjustment {row['adj_id']} of {company} is {row['status']}; only a proposed "
                    f"adjustment can be {status}. Nothing changed"
                )
        for adj_id in ids:
            con.execute(
                "UPDATE adjustments SET status = ?, reason = ?, status_run_id = ?, status_at = ? "
                "WHERE adj_id = ?",
                [status, reason, run.run_id, utcnow(), adj_id],
            )
            record_event(run, company, con, "adjustment", adj_id)
        return [_get(con, engagement, company, adj_id) for adj_id in ids]


def list_adjustments(run: Run, company: str, status: str | None = None) -> list[dict[str, Any]]:
    """The open engagement's adjustments (only those in `status` when given), by id."""
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        only = "" if status is None else " AND status = ?"
        return fetch_dicts(
            con,
            f"SELECT * FROM adjustments WHERE engagement_id = ?{only} ORDER BY adj_id",
            [engagement["engagement_id"], *([] if status is None else [status])],
        )


def format_adjustment(row: dict[str, Any]) -> str:
    account = f" {row['account']}" if row["account"] else ""
    evidence = "; ".join(
        f"{name} {value}"
        for name, value in (
            ("query", row["query_id"]),
            ("finding", row["finding_id"]),
            ("source", row["source_ref"]),
        )
        if value is not None
    )
    lines = [
        f"#{row['adj_id']} {row['status']} {row['period']} {row['scope']} {row['category']}"
        f"{account} {rupees(row['amount'])}: {row['description']}",
        f"    evidence: {evidence}",
    ]
    if row["reason"] is not None:
        lines.append(f"    reason: {row['reason']}")
    return "\n".join(lines)
