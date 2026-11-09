"""Gates (fin.diligence.gates) on a small company database: checklists, approval in order, and a
gate reopening when the data under it changes."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from fin.audit.errors import GateNotApproved, GateNotReady
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence.gates import Context, approve_in, evaluate, require


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Engagement #1 on FY24,FY25 with one loaded document (FY24 figures) and one held one."""
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', now())"
        )
        add_document(con, 1, "a.xlsx", "loaded", "FY24")
        add_document(con, 2, "b.xlsx", "held", "FY25")
        yield con


def add_document(
    con: duckdb.DuckDBPyConnection, doc_id: int, name: str, status: str, period: str
) -> None:
    con.execute(
        "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, status, "
        "archive_path, run_id, ingested_at) VALUES (?, ?, ?, 1, ?, 'standalone', ?, 'x', 'r0', "
        "now())",
        [doc_id, name, f"sha-{doc_id}", period, status],
    )
    con.execute(
        "INSERT INTO facts (raw_id, doc_id, fiscal_year, period_end, scope, value, confirmed, "
        "run_id, period, period_months) VALUES (?, ?, ?, DATE '2024-03-31', 'standalone', 1, "
        "true, 'r0', ?, 12)",
        [doc_id, doc_id, period, period],
    )


def raise_draft(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO accountant_queries (engagement_id, origin, question, sources, status, "
        "created_run_id, created_at, status_run_id, status_at) "
        "VALUES (1, 'manual', 'Why?', 'a.xlsx P&L!C7', 'draft', 'r0', now(), 'r0', now())"
    )


def ctx(con: duckdb.DuckDBPyConnection, pending: list[dict[str, Any]] | None = None) -> Context:
    [engagement] = [
        dict(zip(("engagement_id", "periods"), row, strict=True))
        for row in con.execute("SELECT engagement_id, periods FROM engagement").fetchall()
    ]
    return Context(con, "acme", engagement, pending or [])


def states(context: Context) -> list[tuple[str, str, str | None]]:
    return [(g["gate"], g["state"], g["reason"]) for g in evaluate(context)]


def test_g1_checklist_names_what_is_missing(con: duckdb.DuckDBPyConnection) -> None:
    pending = [{"id": 3, "status": "pending", "statement": "x", "section": "", "label": "Misc"}]

    [g1, g2, g4] = evaluate(ctx(con, pending))

    assert [(line["ok"], line["text"]) for line in g1["checklist"]] == [
        (True, "FY24: a loaded document has figures for it"),
        (False, "FY25: no loaded document has figures for it"),
        (False, "held documents: doc_id 2 b.xlsx (`fin findings list acme`)"),
        (False, "1 mapping items waiting for review (`fin review list acme`)"),
    ]
    assert [(line["ok"], line["text"]) for line in g2["checklist"]] == [
        (True, "no queries in draft")
    ]
    assert [(line["ok"], line["text"]) for line in g4["checklist"]] == [
        (True, "no proposed adjustments"),
        (True, "every accepted adjustment has evidence"),
    ]
    assert states(ctx(con)) == [("G1", "open", None), ("G2", "open", None), ("G4", "open", None)]


def test_gates_are_approved_in_order_and_only_when_the_checklist_passes(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    run = Run("gate approve", {}, tmp_path)

    gate_ctx = ctx(con)
    with pytest.raises(GateNotReady) as early:
        approve_in(run, gate_ctx, "G2")
    with pytest.raises(GateNotReady) as failing:
        approve_in(run, gate_ctx, "G1")

    assert str(early.value) == (
        "E802: gate G2 (Queries) of acme cannot be approved before gate G1 (Data), which is "
        "open; nothing approved. See `fin gate status acme`"
    )
    assert str(failing.value) == (
        "E802: gate G1 (Data) of acme cannot be approved; its checklist fails: FY25: no loaded "
        "document has figures for it; held documents: doc_id 2 b.xlsx (`fin findings list "
        "acme`). Nothing approved"
    )
    assert con.execute("SELECT count(*) FROM gate_approvals").fetchall() == [(0,)]

    con.execute("UPDATE documents SET status = 'loaded' WHERE doc_id = 2")
    assert approve_in(run, ctx(con), "G1")["state"] == "approved"
    assert approve_in(run, ctx(con), "G2")["state"] == "approved"

    assert states(ctx(con)) == [
        ("G1", "approved", None),
        ("G2", "approved", None),
        ("G4", "open", None),
    ]
    assert con.execute("SELECT engagement_id, gate FROM gate_approvals").fetchall() == [
        (1, "G1"),
        (1, "G2"),
    ]


def test_a_gate_reopens_when_its_data_changes(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    run = Run("gate approve", {}, tmp_path)
    con.execute("UPDATE documents SET status = 'loaded' WHERE doc_id = 2")
    for name in ("G1", "G2", "G4"):
        approve_in(run, ctx(con), name)
    require(ctx(con), "G4", "fin engagement close")  # approved: no error

    raise_draft(con)

    assert states(ctx(con)) == [
        ("G1", "approved", None),
        ("G2", "reopened", "changed since it was approved: queries"),
        ("G4", "reopened", "G2 is not approved"),
    ]
    gate_ctx = ctx(con)
    with pytest.raises(GateNotApproved) as refused:
        require(gate_ctx, "G2", "fin adjust accept")
    assert str(refused.value) == (
        "E801: `fin adjust accept` needs gate G2 (Queries) approved; it is reopened (changed "
        "since it was approved: queries). Nothing changed. See `fin gate status acme`"
    )
    with pytest.raises(GateNotApproved) as close:
        require(gate_ctx, "G4", "fin engagement close")
    assert str(close.value) == (
        "E801: `fin engagement close` needs gate G4 (Adjustments) approved; gate G2 (Queries) "
        "before it is reopened (changed since it was approved: queries). Nothing changed. See "
        "`fin gate status acme`"
    )

    add_document(con, 3, "c.xlsx", "loaded", "FY25")

    assert states(ctx(con))[0] == ("G1", "reopened", "changed since it was approved: documents")


def test_g1_reopens_when_a_figure_changes(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    run = Run("gate approve", {}, tmp_path)
    con.execute("UPDATE documents SET status = 'loaded' WHERE doc_id = 2")
    con.execute("UPDATE facts SET account = 'revenue'")
    approve_in(run, ctx(con), "G1")
    approve_in(run, ctx(con), "G2")

    con.execute("UPDATE facts SET value = 2 WHERE doc_id = 1")

    assert states(ctx(con)) == [
        ("G1", "reopened", "changed since it was approved: figures"),
        ("G2", "reopened", "G1 is not approved"),
        ("G4", "open", None),
    ]


def test_g2_stays_approved_as_an_approved_query_moves_on_and_reopens_when_reworded(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    run = Run("gate approve", {}, tmp_path)
    con.execute("UPDATE documents SET status = 'loaded' WHERE doc_id = 2")
    raise_draft(con)
    con.execute("UPDATE accountant_queries SET status = 'approved'")
    approve_in(run, ctx(con), "G1")
    approve_in(run, ctx(con), "G2")

    for status in ("sent", "answered", "closed"):
        con.execute("UPDATE accountant_queries SET status = ?", [status])
        assert states(ctx(con))[1] == ("G2", "approved", None)

    con.execute("UPDATE accountant_queries SET question = 'Why, exactly?'")

    assert states(ctx(con))[1] == ("G2", "reopened", "changed since it was approved: queries")
