"""Accountant queries (`fin query ...`): questions for the company's accountant.

A difference in diligence data is the company's: it is queried, never fixed. Claude drafts a query
(raised from a finding, or by hand with its amount, period and sources); the user approves it; the
user sends it and says so (`fin query sent`: fin never sends anything); the answer is recorded and
the query closed. Status flow (table `accountant_queries`, db/schema/diligence.sql):

    draft -> approved -> sent -> answered -> closed, and withdrawn from any status before closed.

Raising a query from a finding copies its period, scope, difference and sources; the finding itself
is not changed. Every query raised, edited or changed is appended to the audit table
`diligence_events`.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import DiligenceChangeRefused
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, record_event, require_open, rupees

# target status -> the statuses a query may move to it from
TRANSITIONS = {
    "approved": ("draft",),
    "sent": ("approved",),
    "answered": ("sent",),
    "closed": ("answered",),
    "withdrawn": ("draft", "approved", "sent", "answered"),
}


def allowed(current: str, target: str) -> bool:
    """Whether a query in status `current` may become `target`."""
    return current in TRANSITIONS.get(target, ())


def finding_sources(finding: dict[str, Any]) -> str:
    """Where a finding's two figures are: '<file> <sheet!cell or page>; <other file> <ref>'."""
    parts = []
    if finding["fin_source"]:
        parts.append(f"{finding['file_name']} {finding['fin_source']}")
    if finding["other_source"]:
        other_file = finding["other_file"] or finding["file_name"]
        parts.append(f"{other_file} {finding['other_source']}")
    return "; ".join(parts) or (finding["file_name"] or f"finding {finding['finding_id']}")


def _get(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], company: str, query_id: int
) -> dict[str, Any]:
    rows = fetch_dicts(
        con,
        "SELECT * FROM accountant_queries WHERE query_id = ? AND engagement_id = ?",
        [query_id, engagement["engagement_id"]],
    )
    if not rows:
        raise DiligenceChangeRefused(
            f"query {query_id} is not a query of {company}'s open engagement "
            f"#{engagement['engagement_id']}; nothing changed. Queries: `fin query list {company}`"
        )
    return rows[0]


def _insert(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    engagement: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    now = utcnow()
    row = {
        "engagement_id": engagement["engagement_id"],
        **values,
        "status": "draft",
        "created_run_id": run.run_id,
        "created_at": now,
        "status_run_id": run.run_id,
        "status_at": now,
    }
    new = con.execute(
        f"INSERT INTO accountant_queries ({', '.join(row)}) "
        f"VALUES ({', '.join('?' for _ in row)}) RETURNING query_id",
        list(row.values()),
    ).fetchone()
    assert new is not None
    record_event(run, company, con, "query", new[0])
    return _get(con, engagement, company, new[0])


def raise_from_finding(run: Run, company: str, finding_id: int, question: str) -> dict[str, Any]:
    """A draft query on an open or accepted finding: its period, scope, difference (as the amount)
    and sources. E805 for an unknown finding or one no longer open or accepted."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        rows = fetch_dicts(con, "SELECT * FROM findings WHERE finding_id = ?", [finding_id])
        if not rows or rows[0]["status"] not in ("open", "accepted"):
            state = "does not exist" if not rows else f"is {rows[0]['status']}"
            raise DiligenceChangeRefused(
                f"finding {finding_id} of {company} {state}; nothing raised. Raise queries from "
                f"open or accepted findings: `fin findings list {company}`"
            )
        finding = rows[0]
        values = {
            "origin": "finding",
            "finding_id": finding_id,
            "period": finding["period"],
            "scope": finding["scope"],
            "question": question,
            "amount": finding["difference"],
            "sources": finding_sources(finding),
        }
        return _insert(run, company, con, engagement, values)


def raise_manual(
    run: Run,
    company: str,
    *,
    amount: Decimal,
    period: str,
    scope: str | None,
    sources: str,
    question: str,
) -> dict[str, Any]:
    """A draft query with the amount, period, scope and sources given."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        values = {
            "origin": "manual",
            "finding_id": None,
            "period": period,
            "scope": scope,
            "question": question,
            "amount": amount,
            "sources": sources,
        }
        return _insert(run, company, con, engagement, values)


def edit(run: Run, company: str, query_id: int, question: str) -> dict[str, Any]:
    """Change a draft's wording. E805 unless the query is a draft."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        row = _get(con, engagement, company, query_id)
        if row["status"] != "draft":
            raise DiligenceChangeRefused(
                f"query {query_id} of {company} is {row['status']}; only a draft can be edited. "
                "Nothing changed"
            )
        con.execute(
            "UPDATE accountant_queries SET question = ? WHERE query_id = ?", [question, query_id]
        )
        record_event(run, company, con, "query", query_id)
        return _get(con, engagement, company, query_id)


def set_status(
    run: Run,
    company: str,
    ids: list[int],
    target: str,
    *,
    answer: str | None = None,
    answer_source: str | None = None,
    note: str | None = None,
) -> list[dict[str, Any]]:
    """Move every query in `ids` to `target` (with the answer, or the reason for a withdrawal), all
    or none. E805 for an unknown id or a change TRANSITIONS does not allow."""
    ids = list(dict.fromkeys(ids))
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        for row in [_get(con, engagement, company, query_id) for query_id in ids]:
            if not allowed(row["status"], target):
                before = " or ".join(TRANSITIONS[target])
                raise DiligenceChangeRefused(
                    f"query {row['query_id']} of {company} is {row['status']}; only a query that "
                    f"is {before} can become {target}. Nothing changed"
                )
        for query_id in ids:
            con.execute(
                "UPDATE accountant_queries SET status = ?, status_run_id = ?, status_at = ?, "
                "answer = coalesce(?, answer), answer_source = coalesce(?, answer_source), "
                "note = coalesce(?, note) WHERE query_id = ?",
                [target, run.run_id, utcnow(), answer, answer_source, note, query_id],
            )
            record_event(run, company, con, "query", query_id)
        return [_get(con, engagement, company, query_id) for query_id in ids]


def list_queries(run: Run, company: str, status: str | None = None) -> list[dict[str, Any]]:
    """The open engagement's queries (only those in `status` when given), by id."""
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        only = "" if status is None else " AND status = ?"
        return fetch_dicts(
            con,
            f"SELECT * FROM accountant_queries WHERE engagement_id = ?{only} ORDER BY query_id",
            [engagement["engagement_id"], *([] if status is None else [status])],
        )


def format_query(row: dict[str, Any]) -> str:
    origin = f"finding {row['finding_id']}" if row["origin"] == "finding" else "manual"
    where = " ".join(x for x in (row["period"], row["scope"]) if x) or "-"
    lines = [
        f"#{row['query_id']} {row['status']} {where} {rupees(row['amount'])} ({origin}): "
        f"{row['question']}",
        f"    sources: {row['sources']}",
    ]
    if row["answer"] is not None:
        source = f" [{row['answer_source']}]" if row["answer_source"] else ""
        lines.append(f"    answer: {row['answer']}{source}")
    if row["note"] is not None:
        lines.append(f"    withdrawn: {row['note']}")
    return "\n".join(lines)
