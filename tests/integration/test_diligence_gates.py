"""Diligence gates (fin.diligence.gates) through the CLI: approved in order and only when their
checklist passes; accepting an adjustment needs G2, closing the engagement needs G4; a gate reopens
when the data under it changes; every approval in `diligence_events`."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app

runner = CliRunner()
FIRST = "Bluepeak_Polymers_FY2023-24.xlsx"
SECOND = "Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx"


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path, samples: Path) -> Path:
    """Bluepeak FY24 loaded, and FY25 held: the second document has four open E404 findings."""
    assert fin("init").exit_code == 0
    assert fin("ingest", "bluepeak", str(samples / "clean" / "bluepeak" / FIRST)).exit_code == 0
    assert fin("ingest", "bluepeak", str(samples / "broken" / SECOND)).exit_code == 0
    assert fin("engagement", "open", "bluepeak", "--periods", "FY24,FY25").exit_code == 0
    return data_root


def propose(amount: str) -> None:
    proposed = fin(
        "adjust", "propose", "bluepeak", "--period", "FY25", "--scope", "standalone",
        "--category", "normalisation", "--amount", amount, "--description", "One-off fee",
        "--source", "x.xlsx P&L!C9",
    )  # fmt: skip
    assert proposed.exit_code == 0, proposed.output


def test_gates_unlock_in_order_and_reopen_when_data_changes(root: Path, audit_query: Any) -> None:
    propose("-500000")

    # Refused before any gate is approved; nothing changed.
    refused = fin("adjust", "accept", "bluepeak", "1")
    assert refused.exit_code == 3
    assert refused.stderr.startswith(
        "E801: `fin adjust accept` needs gate G2 (Queries) approved; gate G1 (Data) before it is "
        "open. Nothing changed. See `fin gate status bluepeak`\n"
    )
    early = fin("gate", "approve", "bluepeak", "G2")
    assert early.exit_code == 3
    assert early.stderr.startswith(
        "E802: gate G2 (Queries) of bluepeak cannot be approved before gate G1 (Data), which is "
        "open; nothing approved. See `fin gate status bluepeak`\n"
    )
    held = fin("gate", "approve", "bluepeak", "g1")
    assert held.exit_code == 3
    assert held.stderr.startswith(
        "E802: gate G1 (Data) of bluepeak cannot be approved; its checklist fails: FY25: no "
        f"loaded document has mapped figures for it; held documents: doc_id 2 {SECOND} "
        "(`fin findings list bluepeak`). Nothing approved\n"
    )

    # The user saves the held document as filed, then approves G1 and G2.
    assert fin("findings", "accept", "bluepeak", "1", "2", "3", "4").exit_code == 0
    g1 = fin("gate", "approve", "bluepeak", "G1")
    assert g1.exit_code == 0, g1.output
    assert g1.stdout == (
        "bluepeak: G1 Data: approved\n"
        "    pass: FY24: a loaded document has mapped figures for it\n"
        "    pass: FY25: a loaded document has mapped figures for it\n"
        "    pass: no held documents\n"
        "    pass: no mapping items waiting for review\n"
        "    unlocks: approving G2\n"
    )
    assert fin("gate", "approve", "bluepeak", "G2").exit_code == 0
    accepted = fin("adjust", "accept", "bluepeak", "1")
    assert accepted.exit_code == 0, accepted.output

    # A new draft query reopens G2: accepting is refused again.
    raised = fin(
        "query", "raise", "bluepeak", "--amount", "250000", "--period", "FY25",
        "--source", "ledger.xlsx Sales!D40", "--question", "What is this credit?",
    )  # fmt: skip
    assert raised.exit_code == 0, raised.output
    propose("-100000")
    reopened = fin("adjust", "accept", "bluepeak", "2")
    assert reopened.exit_code == 3
    assert reopened.stderr.startswith(
        "E801: `fin adjust accept` needs gate G2 (Queries) approved; it is reopened (changed "
        "since it was approved: queries). Nothing changed. See `fin gate status bluepeak`\n"
    )
    close = fin("engagement", "close", "bluepeak")
    assert close.exit_code == 3
    assert close.stderr.startswith(
        "E801: `fin engagement close` needs gate G4 (Adjustments) approved; gate G2 (Queries) "
        "before it is reopened (changed since it was approved: queries). Nothing changed."
    )

    status = json.loads(fin("status", "bluepeak", "--json").stdout)
    assert status[0]["diligence"] == {
        "engagement_id": 1,
        "periods": "FY24,FY25",
        "queries": {"draft": 1},
        "proposed_adjustments": 1,
        "gates": {"G1": "approved", "G2": "reopened", "G4": "open"},
    }
    assert fin("status", "bluepeak").stdout.endswith(
        "  engagement #1 (FY24,FY25): queries draft 1; proposed adjustments 1\n"
        "  gates: G1 approved, G2 reopened, G4 open\n"
    )
    gate_status = fin("gate", "status", "bluepeak")
    assert gate_status.exit_code == 0, gate_status.output
    assert (
        "G2 Queries: reopened (changed since it was approved: queries)\n"
        "    FAIL: queries in draft: #1 (approve or withdraw them: "
        "`fin query list bluepeak --status draft`)\n"
        "    unlocks: `fin adjust accept|reject`\n"
        "G4 Adjustments: open\n"
        "    FAIL: proposed adjustments: #2 (accept or reject them: "
        "`fin adjust list bluepeak --status proposed`)\n"
        "    pass: every accepted adjustment has evidence\n"
        "    unlocks: `fin engagement close`\n"
    ) in gate_status.stdout

    # The user withdraws the query, approves G2 again, rejects #2, approves G4: close works.
    assert fin("query", "withdraw", "bluepeak", "1", "--reason", "raised twice").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G2").exit_code == 0
    assert fin("adjust", "reject", "bluepeak", "2", "--reason", "recurring").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G4").exit_code == 0
    closed = fin("engagement", "close", "bluepeak")
    assert closed.exit_code == 0, closed.output

    events = audit_query(
        "SELECT engagement_id, item_id, status, detail FROM diligence_events WHERE kind = 'gate' "
        "ORDER BY recorded_at"
    )
    assert [(e, item, s, json.loads(detail)["gate"]) for e, item, s, detail in events] == [
        (1, "G1", "approved", "G1"),
        (1, "G2", "approved", "G2"),
        (1, "G2", "approved", "G2"),
        (1, "G4", "approved", "G4"),
    ]


def test_a_changed_figure_reopens_g1(root: Path) -> None:
    propose("-500000")
    assert fin("findings", "accept", "bluepeak", "1", "2", "3", "4").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G1").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G2").exit_code == 0

    fixed = fin(
        "sql", "bluepeak", "--write", "--sql",
        "UPDATE facts SET value = value + 5000000 WHERE account = 'depreciation' "
        "AND period = 'FY25'",
    )  # fmt: skip
    assert fixed.exit_code == 0, fixed.output

    status = json.loads(fin("gate", "status", "bluepeak", "--json").stdout)
    assert [(g["gate"], g["state"], g["reason"]) for g in status["gates"]] == [
        ("G1", "reopened", "changed since it was approved: figures"),
        ("G2", "reopened", "G1 is not approved"),
        ("G4", "open", None),
    ]
    refused = fin("adjust", "accept", "bluepeak", "1")
    assert refused.exit_code == 3
    assert refused.stderr.startswith(
        "E801: `fin adjust accept` needs gate G2 (Queries) approved; gate G1 (Data) before it is "
        "reopened (changed since it was approved: figures). Nothing changed. See `fin gate "
        "status bluepeak`\n"
    )


DILIGENCE_ROWS = (
    "SELECT (SELECT count(*) FROM gate_approvals) AS approvals, "
    "(SELECT string_agg(status, ',') FROM adjustments) AS adjustments, "
    "(SELECT string_agg(status, ',') FROM accountant_queries) AS queries"
)
UNCHANGED = [{"approvals": 0, "adjustments": "proposed", "queries": "draft"}]


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        (
            "INSERT INTO gate_approvals (engagement_id, gate, input_hash, input_hashes, "
            "checklist, approved_run_id, approved_at) "
            "VALUES (1, 'G2', 'h', '{}', '[]', 'x', now())",
            "gate_approvals",
        ),
        ("UPDATE adjustments SET status = 'accepted'", "adjustments"),
        (
            "UPDATE facts SET value = 0.01; DELETE FROM main.Accountant_Queries",
            "accountant_queries",
        ),
    ],
)
def test_sql_write_refuses_the_diligence_tables(
    root: Path, audit_query: Any, sql: str, found: str
) -> None:
    propose("-500000")
    raised = fin(
        "query", "raise", "bluepeak", "--amount", "250000", "--period", "FY25",
        "--source", "ledger.xlsx Sales!D40", "--question", "What is this credit?",
    )  # fmt: skip
    assert raised.exit_code == 0, raised.output
    assert json.loads(fin("sql", "bluepeak", "--sql", DILIGENCE_ROWS).stdout)["rows"] == UNCHANGED
    ones = "SELECT count(*) AS ones FROM facts WHERE value = 0.01"
    assert json.loads(fin("sql", "bluepeak", "--sql", ones).stdout)["rows"] == [{"ones": 0}]

    result = fin("sql", "bluepeak", "--write", "--sql", sql)

    assert result.exit_code == 2
    assert result.stderr.startswith(
        f"E806: `fin sql --write` does not change the diligence tables (found: {found}); use "
        "`fin engagement`, `fin query`, `fin adjust` or `fin gate`. Nothing was run\n"
    )
    assert json.loads(fin("sql", "bluepeak", "--sql", DILIGENCE_ROWS).stdout)["rows"] == UNCHANGED
    assert json.loads(fin("sql", "bluepeak", "--sql", ones).stdout)["rows"] == [{"ones": 0}]
    assert audit_query("SELECT mode, row_count, backup_path FROM queries WHERE sql = ?", [sql]) == [
        ("write", None, None)
    ]
    assert audit_query("SELECT error_code FROM events WHERE error_code = 'E806'") == [("E806",)]


def test_unknown_gate_is_a_usage_error(root: Path) -> None:
    result = fin("gate", "approve", "bluepeak", "G3")

    assert result.exit_code == 2
    assert "use one of: G1, G2, G4" in result.output


def test_sending_an_approved_query_keeps_g2_approved(root: Path) -> None:
    raised = fin(
        "query", "raise", "bluepeak", "--finding", "2", "--question", "Why does it differ?"
    )
    assert raised.exit_code == 0, raised.output
    assert fin("query", "approve", "bluepeak", "1").exit_code == 0
    assert fin("findings", "accept", "bluepeak", "1", "2", "3", "4").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G1").exit_code == 0
    assert fin("gate", "approve", "bluepeak", "G2").exit_code == 0

    sent = fin("query", "sent", "bluepeak", "1")

    assert sent.exit_code == 0, sent.output
    status = json.loads(fin("gate", "status", "bluepeak", "--json").stdout)
    assert [(g["gate"], g["state"]) for g in status["gates"]] == [
        ("G1", "approved"),
        ("G2", "approved"),
        ("G4", "open"),
    ]
