"""Diligence (fin.diligence): an engagement, accountant queries raised from findings and taken
through their statuses, and adjustments with evidence; every change in `diligence_events`."""

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
    """Bluepeak with an E404 finding: FY24 revenue differs between its two documents (finding 2)."""
    assert fin("init").exit_code == 0
    assert fin("ingest", "bluepeak", str(samples / "clean" / "bluepeak" / FIRST)).exit_code == 0
    assert fin("ingest", "bluepeak", str(samples / "broken" / SECOND)).exit_code == 0
    return data_root


def approve_gates(*names: str) -> None:
    """Save the held document as filed (its findings accepted), so gate G1 passes, then approve
    the gates named, in order."""
    assert fin("findings", "accept", "bluepeak", "1", "2", "3", "4").exit_code == 0
    for name in names:
        approved = fin("gate", "approve", "bluepeak", name)
        assert approved.exit_code == 0, approved.output


def test_every_command_needs_an_open_engagement(root: Path) -> None:
    for args in (
        ("engagement", "status", "bluepeak"),
        ("engagement", "close", "bluepeak"),
        ("query", "list", "bluepeak"),
        ("adjust", "list", "bluepeak"),
    ):
        result = fin(*args)
        assert result.exit_code == 2, args
        assert result.stderr.startswith("E803: bluepeak has no open engagement; nothing changed.")


def test_engagement_opens_once_and_closes(root: Path, audit_query: Any) -> None:
    opened = fin("engagement", "open", "bluepeak", "--periods", "FY25,FY24")

    assert opened.exit_code == 0, opened.output
    assert opened.stdout.startswith(
        "bluepeak: engagement #1 open: periods FY24,FY25, query threshold ₹100,000.00, opened "
    )
    again = fin("engagement", "open", "bluepeak", "--periods", "FY25", "--threshold", "5000")
    assert again.exit_code == 2
    assert again.stderr.startswith(
        "E803: bluepeak already has an open engagement (#1, periods FY24,FY25); nothing changed."
    )

    approve_gates("G1", "G2", "G4")
    closed = fin("engagement", "close", "bluepeak")
    assert closed.exit_code == 0, closed.output
    reopened = fin("engagement", "open", "bluepeak", "--periods", "FY25", "--threshold", "5000")
    assert reopened.exit_code == 0, reopened.output
    status = json.loads(fin("engagement", "status", "bluepeak", "--json").stdout)
    assert (status["engagement"]["engagement_id"], status["engagement"]["periods"]) == (2, "FY25")
    assert status["engagement"]["query_threshold"] == "5000.00"
    assert audit_query(
        "SELECT engagement_id, kind, item_id, status FROM diligence_events "
        "WHERE kind = 'engagement' ORDER BY recorded_at"
    ) == [
        (1, "engagement", "1", "open"),
        (1, "engagement", "1", "closed"),
        (2, "engagement", "2", "open"),
    ]


def test_query_from_a_finding_goes_draft_to_closed(root: Path, audit_query: Any) -> None:
    assert fin("engagement", "open", "bluepeak", "--periods", "FY24,FY25").exit_code == 0

    raised = fin("query", "raise", "bluepeak", "--finding", "2", "--question", "Why?")

    assert raised.exit_code == 0, raised.output
    assert raised.stdout == (
        "#1 draft FY24 standalone -₹35,000,000.00 (finding 2): Why?\n"
        f"    sources: {SECOND} Standalone P&L!C7; {FIRST} Standalone P&L!B7\n"
    )
    # Raising a query does not change the finding.
    findings = json.loads(fin("findings", "list", "bluepeak", "--json").stdout)
    assert [f["status"] for f in findings] == ["open"] * 4

    # Refused: not yet approved, so it cannot be sent; nothing changed.
    refused = fin("query", "sent", "bluepeak", "1")
    assert refused.exit_code == 2
    assert refused.stderr.startswith(
        "E805: query 1 of bluepeak is draft; only a query that is approved can become sent."
    )
    assert fin("query", "edit", "bluepeak", "1", "--question", "Why does it differ?").exit_code == 0
    assert fin("query", "approve", "bluepeak", "1").exit_code == 0
    edit_refused = fin("query", "edit", "bluepeak", "1", "--question", "Other")
    assert edit_refused.exit_code == 2
    assert edit_refused.stderr.startswith("E805: query 1 of bluepeak is approved; only a draft")
    assert fin("query", "sent", "bluepeak", "1").exit_code == 0
    answered = fin("query", "answer", "bluepeak", "1", "--text", "Restated", "--source", "mail")
    assert answered.exit_code == 0, answered.output
    assert fin("query", "close", "bluepeak", "1").exit_code == 0
    withdraw = fin("query", "withdraw", "bluepeak", "1", "--reason", "late")
    assert withdraw.exit_code == 2
    assert withdraw.stderr.startswith("E805: query 1 of bluepeak is closed;")
    unknown = fin("query", "approve", "bluepeak", "7")
    assert unknown.exit_code == 2
    assert unknown.stderr.startswith("E805: query 7 is not a query of bluepeak's open engagement")

    [query] = json.loads(fin("query", "list", "bluepeak", "--json").stdout)
    assert {
        key: query[key]
        for key in ("query_id", "origin", "finding_id", "period", "scope", "question", "amount")
    } == {
        "query_id": 1,
        "origin": "finding",
        "finding_id": 2,
        "period": "FY24",
        "scope": "standalone",
        "question": "Why does it differ?",
        "amount": "-35000000.00",
    }
    assert (query["status"], query["answer"], query["answer_source"]) == (
        "closed",
        "Restated",
        "mail",
    )
    events = audit_query(
        "SELECT item_id, status, detail FROM diligence_events WHERE kind = 'query' "
        "ORDER BY recorded_at"
    )
    assert [(item, status, json.loads(detail)["question"]) for item, status, detail in events] == [
        ("1", "draft", "Why?"),
        ("1", "draft", "Why does it differ?"),
        ("1", "approved", "Why does it differ?"),
        ("1", "sent", "Why does it differ?"),
        ("1", "answered", "Why does it differ?"),
        ("1", "closed", "Why does it differ?"),
    ]


def test_manual_query_and_withdrawal(root: Path) -> None:
    assert fin("engagement", "open", "bluepeak", "--periods", "FY25").exit_code == 0

    raised = fin(
        "query", "raise", "bluepeak", "--amount", "250000.50", "--period", "FY25",
        "--source", "ledger.xlsx Sales!D40", "--question", "What is this credit?",
    )  # fmt: skip

    assert raised.exit_code == 0, raised.output
    assert raised.stdout.startswith("#1 draft FY25 ₹250,000.50 (manual): What is this credit?\n")
    withdrawn = fin("query", "withdraw", "bluepeak", "1", "--reason", "raised twice")
    assert withdrawn.exit_code == 0, withdrawn.output
    assert withdrawn.stdout.endswith("    withdrawn: raised twice\n")
    assert fin("query", "list", "bluepeak", "--status", "draft").stdout == "no queries\n"


def test_adjustments_need_evidence_and_are_accepted_or_rejected(
    root: Path, audit_query: Any
) -> None:
    assert fin("engagement", "open", "bluepeak", "--periods", "FY24,FY25").exit_code == 0
    base = (
        "adjust", "propose", "bluepeak", "--period", "FY25", "--scope", "standalone",
        "--category", "normalisation", "--amount", "-500000", "--description", "One-off fee",
    )  # fmt: skip

    no_evidence = fin(*base)
    assert no_evidence.exit_code == 2
    assert no_evidence.stderr.startswith(
        "E804: an adjustment needs evidence: --query, --finding or --source; nothing proposed"
    )
    wrong_period = fin(*base[:4], "FY23", *base[5:], "--source", "x.xlsx P&L!C9")
    assert wrong_period.exit_code == 2
    assert wrong_period.stderr.startswith("E804: period FY23 is not in the engagement (FY24,FY25)")
    wrong_category = fin(*base[:8], "tax", *base[9:], "--source", "x.xlsx P&L!C9")
    assert wrong_category.exit_code == 2
    assert wrong_category.stderr.startswith("E804: category 'tax' is not in config.toml")
    unknown_account = fin(*base, "--source", "x.xlsx P&L!C9", "--account", "no_such_account")
    assert unknown_account.exit_code == 2
    assert unknown_account.stderr.startswith("E804: account 'no_such_account' is not in")
    assert fin("adjust", "list", "bluepeak").stdout == "no adjustments\n"

    first = fin(*base, "--source", "x.xlsx P&L!C9", "--account", "other_expenses")
    second = fin(*base[:4], "FY24", *base[5:], "--finding", "1")
    assert first.exit_code == 0, first.output
    assert first.stdout == (
        "#1 proposed FY25 standalone normalisation other_expenses -₹500,000.00: One-off fee\n"
        "    evidence: source x.xlsx P&L!C9\n"
    )
    assert second.exit_code == 0, second.output

    approve_gates("G1", "G2")  # accepting and rejecting need gate G2
    assert fin("adjust", "accept", "bluepeak", "1").exit_code == 0
    rejected = fin("adjust", "reject", "bluepeak", "2", "--reason", "not recurring")
    assert rejected.exit_code == 0, rejected.output
    again = fin("adjust", "accept", "bluepeak", "2")
    assert again.exit_code == 2
    assert again.stderr.startswith(
        "E805: adjustment 2 of bluepeak is rejected; only a proposed adjustment can be accepted."
    )

    listed = json.loads(fin("adjust", "list", "bluepeak", "--json").stdout)
    assert [(a["adj_id"], a["status"], a["period"], a["reason"]) for a in listed] == [
        (1, "accepted", "FY25", None),
        (2, "rejected", "FY24", "not recurring"),
    ]
    status = json.loads(fin("engagement", "status", "bluepeak", "--json").stdout)
    assert status["adjustments"] == {
        "proposed": {"count": 0, "amount": "0.00"},
        "accepted": {"count": 1, "amount": "-500000.00"},
        "rejected": {"count": 1, "amount": "-500000.00"},
    }
    assert audit_query(
        "SELECT item_id, status FROM diligence_events WHERE kind = 'adjustment' "
        "ORDER BY recorded_at"
    ) == [("1", "proposed"), ("2", "proposed"), ("1", "accepted"), ("2", "rejected")]
