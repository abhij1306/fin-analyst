"""From a reference difference, one step to a draft query or a proposed adjustment (SPEC-10). Both
carry the two sources: fin's file and sheet!cell (or page) and the reference's. Nothing is changed
to make the figures tie: the query is a draft for the user to approve, the adjustment a proposal
for the user to accept (`fin.diligence`)."""

from decimal import Decimal
from typing import Any

from fin.audit.errors import ReferenceDifferenceUnknown
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.diligence import adjustments, queries
from fin.paths import company_db_path


def difference(run: Run, company: str, value_id: int) -> dict[str, Any]:
    """The comparison row of a reference figure that differs from fin's. E982 otherwise."""
    with connect(company_db_path(run.root, company)) as con:
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


def raise_query(run: Run, company: str, value_id: int, question: str) -> dict[str, Any]:
    """A draft query: the difference (fin minus the reference) as its amount, the figure's period
    and scope, both sources. E982, or the query's own refusals (E803, E881, E861)."""
    row = difference(run, company, value_id)
    return queries.raise_manual(
        run,
        company,
        amount=row["difference"],
        period=row["period"],
        scope=row["scope"],
        sources=sources(row),
        question=question,
    )


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
