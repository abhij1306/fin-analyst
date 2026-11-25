"""`fin audit <company>` lists the diligence changes (queries, adjustments) with their run ids."""

from decimal import Decimal
from pathlib import Path

from fin.audit.logger import Run
from fin.audit.trace import company_history, format_history
from fin.diligence import adjustments, queries
from fin.diligence.adjustments import Evidence
from tests.engagement_root import engagement_root


def test_audit_lists_a_raised_query_and_a_proposed_adjustment(tmp_path: Path) -> None:
    root = engagement_root(tmp_path)
    query_run, adjust_run = Run("query raise", {}, root), Run("adjust propose", {}, root)
    query = queries.raise_manual(
        query_run,
        "acme",
        amount=Decimal("500000"),
        period="FY25",
        scope=None,
        sources="a.xlsx Sheet!A1",
        question="Why?",
    )
    adjustment = adjustments.propose(
        adjust_run,
        "acme",
        period="FY25",
        scope="standalone",
        category="error",
        description="One-off",
        amount=Decimal("-400000"),
        evidence=Evidence(query_id=query["query_id"]),
    )

    query_run.flush()
    adjust_run.flush()

    history = company_history(root, "acme")
    text = format_history(history)

    assert [(e["kind"], e["item_id"], e["run_id"]) for e in history["diligence_events"]] == [
        ("query", str(query["query_id"]), query_run.run_id),
        ("adjustment", str(adjustment["adj_id"]), adjust_run.run_id),
    ]
    assert "diligence changes:" in text
    assert f"query {query['query_id']} draft (run {query_run.run_id})" in text
    assert f"adjustment {adjustment['adj_id']} proposed (run {adjust_run.run_id})" in text
