"""From a reference difference, one step to a draft query or a proposed adjustment (SPEC-10). Both
carry the two sources: fin's file and sheet!cell (or page) and the reference's. Nothing is changed
to make the figures tie: the query is a draft for the user to approve, the adjustment a proposal
for the user to accept (`fin.diligence`)."""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReferenceDifferenceUnknown, ReferenceNotQueryable
from fin.audit.logger import Run
from fin.db.conn import connect, transaction
from fin.db.rows import fetch_dicts
from fin.diligence import adjustments, queries
from fin.diligence.engagement import company_database, require_open, rupees
from fin.paths import company_db_path


def difference(run: Run, company: str, value_id: int) -> dict[str, Any]:
    """The comparison row of a reference figure that differs from fin's. E982 otherwise."""
    with connect(company_db_path(run.root, company)) as con:
        return _difference(con, company, value_id)


def _difference(con: duckdb.DuckDBPyConnection, company: str, value_id: int) -> dict[str, Any]:
    rows = fetch_dicts(con, "SELECT * FROM reference_comparison WHERE value_id = ?", [value_id])
    if not rows or rows[0]["state"] != "differs":
        state = "no such figure" if not rows else rows[0]["state"]
        raise ReferenceDifferenceUnknown(
            f"reference figure #{value_id} is not a difference ({state}); nothing raised or "
            f"proposed (fin reference compare {company})"
        ).with_context(value_id=value_id)
    return rows[0]


def sources(row: dict[str, Any]) -> str:
    """'fin: <file> <ref>; <source>: <file> <ref>'."""
    reference = f"{row['source']}: {row['file_name']} {row['reference_ref']}"
    return f"fin: {row['fin_source']}; {reference}"


def _check_queryable(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], company: str, row: dict[str, Any]
) -> None:
    """E983 unless the difference is at or above the engagement's query threshold and the figure
    is in no query of the engagement that is not withdrawn (as `fin query raise --items` and the
    `discrepancies` view's `queryable`)."""
    threshold = engagement["query_threshold"]
    if abs(row["difference"]) < threshold:
        raise ReferenceNotQueryable(
            f"reference figure #{row['value_id']} of {company} differs by "
            f"{rupees(row['difference'])}, below the query threshold {rupees(threshold)}; "
            "nothing raised"
        ).with_context(value_id=row["value_id"], difference=row["difference"])
    linked = con.execute(
        "SELECT q.query_id, q.status FROM reference_values v JOIN accountant_queries q "
        "ON q.query_id = v.query_id AND q.engagement_id = ? "
        "WHERE v.value_id = ? AND q.status <> 'withdrawn'",
        [engagement["engagement_id"], row["value_id"]],
    ).fetchone()
    if linked is not None:
        raise ReferenceNotQueryable(
            f"reference figure #{row['value_id']} of {company} is already in query "
            f"#{linked[0]} ({linked[1]}); nothing raised. Withdraw it first to raise a new one"
        ).with_context(value_id=row["value_id"], query_id=linked[0])


def raise_query(run: Run, company: str, value_id: int, question: str) -> dict[str, Any]:
    """A draft query: the difference (fin minus the reference) as its amount, the figure's period
    and scope, both sources; the figure keeps the link (`reference_values.query_id`). E982,
    E983, or the query's own refusals (E803, E881, E861)."""
    difference(run, company, value_id)  # E982 before the backup
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        row = _difference(con, company, value_id)  # as it is in this transaction
        _check_queryable(con, engagement, company, row)
        query = queries.manual_in(
            run,
            company,
            con,
            engagement,
            amount=row["difference"],
            period=row["period"],
            scope=row["scope"],
            sources=sources(row),
            question=question,
        )
        con.execute(
            "UPDATE reference_values SET query_id = ? WHERE value_id = ?",
            [query["query_id"], value_id],
        )
        return query


def propose_adjustment(
    run: Run, company: str, value_id: int, *, category: str, description: str, amount: Decimal
) -> dict[str, Any]:
    """A proposed adjustment of `amount` (the effect on EBITDA, which the assistant states) for
    the figure's period and scope, its account when the line maps to one, with both sources as
    its evidence. E982, or the adjustment's own refusals (E803, E804)."""
    row = difference(run, company, value_id)
    return adjustments.propose(
        run,
        company,
        period=row["period"],
        scope=row["scope"],
        category=category,
        description=description,
        amount=amount,
        account=row["target"] if row["target_kind"] == "account" else None,
        evidence=adjustments.Evidence(source_ref=sources(row)),
    )
