"""Audit fixes in the diligence state machines: a withdrawn query or a resolved finding is not
evidence, decisions after the latest engagement closed refuse, confirm only from proposed, a finding
query outside the engagement, a provision ledger missing from the trial balance, and a refused
answer leaves no archive copy. Synthetic rows only."""

from pathlib import Path
from typing import Any

import pytest

from fin.actor import USER
from fin.audit.errors import (
    DiligenceChangeRefused,
    EngagementState,
    InvalidMapping,
    QueryPeriodInvalid,
    RelatedPartyLinkInvalid,
)
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import adjustments, gates, ledgers, netdebt, provisions, queries
from fin.disclosures import related
from fin.fdd import tax_ledgers
from fin.paths import archive_dir, company_db_path
from fin.services.diligence import Answer, answer_query
from tests.engagement_root import engagement_root
from tests.fdd_bsheet_fixtures import seed


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return engagement_root(tmp_path)


def sql(root: Path, text: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme"), write=True) as con:
        return con.execute(text, args or []).fetchall()


def add_query(root: Path, status: str) -> int:
    [(query_id,)] = sql(
        root,
        "INSERT INTO accountant_queries (engagement_id, origin, question, sources, status, "
        "created_run_id, created_at, status_run_id, status_at) VALUES (1, 'manual', 'Why?', "
        "'a.xlsx PL!C7', ?, 'r0', now(), 'r0', now()) RETURNING query_id",
        [status],
    )
    return int(query_id)


def add_finding(root: Path, status: str, period: str = "FY25") -> int:
    [(finding_id,)] = sql(
        root,
        "INSERT INTO findings (check_name, finding, scope, period, difference, status, run_id, "
        "recorded_at, status_run_id, status_at) VALUES ('cross_document', 'differs', "
        "'standalone', ?, 5, ?, 'r0', now(), 'r0', now()) RETURNING finding_id",
        [period, status],
    )
    return int(finding_id)


def add_adjustment(
    root: Path, status: str, query_id: int | None = None, finding_id: int | None = None
) -> int:
    [(adj_id,)] = sql(
        root,
        "INSERT INTO adjustments (engagement_id, period, scope, category, amount, revenue_effect, "
        "cost_effect, description, query_id, finding_id, status, created_run_id, created_at, "
        "status_run_id, status_at) VALUES (1, 'FY25', 'standalone', 'normalisation', 10, 0, -10, "
        "'one-off', ?, ?, ?, 'r0', now(), 'r0', now()) RETURNING adj_id",
        [query_id, finding_id, status],
    )
    return int(adj_id)


def evidence_line(root: Path) -> tuple[bool, str]:
    with connect(company_db_path(root, "acme")) as con:
        g4 = gates.evaluate(gates.context(con, root, "acme"))[3]
    [line] = [x for x in g4["checklist"] if "evidence" in x["text"]]
    return line["ok"], line["text"]


@pytest.mark.parametrize("dead", ["query", "finding"])
def test_a_withdrawn_query_or_a_resolved_finding_cannot_back_an_accepted_adjustment(
    root: Path, monkeypatch: pytest.MonkeyPatch, dead: str
) -> None:
    monkeypatch.setattr(gates, "require", lambda *args, **kwargs: None)  # G2 is not the point
    if dead == "query":
        adj_id = add_adjustment(root, "proposed", query_id=add_query(root, "withdrawn"))
    else:
        adj_id = add_adjustment(root, "proposed", finding_id=add_finding(root, "resolved"))

    with pytest.raises(DiligenceChangeRefused, match="has no live evidence") as refused:
        adjustments.decide(run(root), "acme", [adj_id], "accepted")

    assert refused.value.code == "E805"
    assert sql(root, "SELECT status FROM adjustments") == [("proposed",)]
    live = add_adjustment(root, "proposed", query_id=add_query(root, "sent"))
    [accepted] = adjustments.decide(run(root), "acme", [live], "accepted")
    assert accepted["status"] == "accepted"


def test_withdrawing_the_query_or_resolving_the_finding_fails_the_g4_checklist(
    root: Path,
) -> None:
    query_id = add_query(root, "sent")
    finding_id = add_finding(root, "open")
    add_adjustment(root, "accepted", query_id=query_id)
    second = add_adjustment(root, "accepted", finding_id=finding_id)
    assert evidence_line(root) == (True, "every accepted adjustment has evidence")

    sql(root, "UPDATE accountant_queries SET status = 'withdrawn'")
    sql(root, "UPDATE findings SET status = 'superseded'")

    ok, text = evidence_line(root)
    assert not ok
    assert text.startswith(f"accepted adjustments without evidence: #1, #{second} ")


def add_classifications(root: Path) -> None:
    sql(
        root,
        "INSERT INTO ledger_accounts (ledger, status, account, confidence, reason, run_id, "
        "changed_at) VALUES ('Debtors', 'proposed', 'trade_receivables', 0.9, 'name', 'r0', now())",
    )
    sql(
        root,
        "INSERT INTO netdebt_classes (ledger, debt_class, status, reason, run_id, changed_at) "
        "VALUES ('Term Loan', 'debt', 'proposed', 'name', 'r0', now())",
    )
    sql(
        root,
        "INSERT INTO tax_ledgers (ledger, status, tax_class, reason, run_id, changed_at) "
        "VALUES ('GST Output', 'proposed', 'gst_output', 'name', 'r0', now())",
    )


def test_classifications_do_not_change_once_the_latest_engagement_is_closed(root: Path) -> None:
    add_classifications(root)
    sql(root, "UPDATE engagement SET status = 'closed'")
    decisions = [
        lambda: ledgers.confirm(run(root), "acme", [1], actor=USER),
        lambda: ledgers.reject(run(root), "acme", [1], actor=USER),
        lambda: netdebt.decide(run(root), "acme", [1], "confirmed"),
        lambda: tax_ledgers.confirm(run(root), "acme", [1], actor=USER),
        lambda: tax_ledgers.reject(run(root), "acme", [1], actor=USER),
    ]
    for decide in decisions:
        with pytest.raises(EngagementState) as refused:
            decide()
        assert refused.value.code == "E803"
    assert sql(root, "SELECT status FROM ledger_accounts") == [("proposed",)]
    assert sql(root, "SELECT status FROM netdebt_classes") == [("proposed",)]
    assert sql(root, "SELECT status FROM tax_ledgers") == [("proposed",)]


def test_without_any_engagement_classifications_are_decided_as_before(root: Path) -> None:
    add_classifications(root)
    sql(root, "DELETE FROM engagement")

    ledgers.confirm(run(root), "acme", [1], actor=USER)
    netdebt.decide(run(root), "acme", [1], "confirmed")
    tax_ledgers.confirm(run(root), "acme", [1], actor=USER)

    assert sql(root, "SELECT status FROM ledger_accounts") == [("confirmed",)]
    assert sql(root, "SELECT status FROM netdebt_classes") == [("confirmed",)]
    assert sql(root, "SELECT status FROM tax_ledgers") == [("confirmed",)]


def test_a_rejected_ledger_or_related_party_link_is_not_confirmed(root: Path) -> None:
    sql(
        root,
        "INSERT INTO ledger_accounts (ledger, status, account, reason, run_id, changed_at) "
        "VALUES ('Debtors', 'rejected', 'trade_receivables', 'name', 'r0', now())",
    )
    sql(
        root,
        "INSERT INTO related_party_links (engagement_id, party, link_kind, target, status, "
        "reason, run_id, changed_at) VALUES (1, 'Party A', 'customer', 'CUST-0001', 'rejected', "
        "'name', 'r0', now())",
    )

    with pytest.raises(InvalidMapping, match="is rejected"):
        ledgers.confirm(run(root), "acme", [1], actor=USER)
    with pytest.raises(RelatedPartyLinkInvalid, match="only a proposed link") as refused:
        related.decide(run(root), "acme", [1], "confirmed", actor=USER)

    assert refused.value.code == "E941"
    assert sql(root, "SELECT status FROM ledger_accounts") == [("rejected",)]
    assert sql(root, "SELECT status FROM related_party_links") == [("rejected",)]


def test_a_query_on_a_finding_outside_the_engagement_periods_is_e881(root: Path) -> None:
    finding_id = add_finding(root, "open", period="FY24")  # the engagement is FY25 only

    with pytest.raises(QueryPeriodInvalid, match="not a period of the open engagement") as refused:
        queries.raise_from_finding(run(root), "acme", finding_id, "Why does it differ?")

    assert refused.value.code == "E881"
    assert sql(root, "SELECT count(*) FROM accountant_queries") == [(0,)]


def test_a_refused_answer_leaves_no_archived_reply(root: Path, tmp_path: Path) -> None:
    query_id = add_query(root, "draft")  # a draft cannot be answered
    reply = tmp_path / "reply.txt"
    reply.write_text("It was a one-off fee.", encoding="utf-8")

    result = answer_query(
        root, "acme", query_id, Answer(text=None, file=reply), via="cli", actor=USER
    )

    assert result.code == "E805"
    folder = archive_dir(root, "acme")
    assert not folder.exists() or list(folder.iterdir()) == []
    assert sql(root, "SELECT status, answer FROM accountant_queries") == [("draft", None)]


def test_a_confirmed_provision_ledger_missing_from_the_trial_balance_is_unknown_not_zero(
    root: Path,
) -> None:
    path = company_db_path(root, "acme")
    path.unlink()  # the F3 company instead: trial balances for FY24 and FY25
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        seed(con)
        con.execute(
            "INSERT INTO provision_ledgers (ledger, kind, status, reason, run_id, changed_at) "
            "VALUES ('Gratuity Fund (old)', 'gratuity', 'confirmed', 'name', 'r0', now())"
        )
        rows = con.execute(
            "SELECT period, provision_inr, ledgers, confirmed FROM provision_balances "
            "WHERE kind = 'gratuity' ORDER BY period"
        ).fetchall()
        absent = provisions.collect(con, load_config(root).fdd)["absent"]

    assert rows and all(r[1:] == (None, 0, True) for r in rows)
    assert sorted((a["period"], a["kind"]) for a in absent) == [(r[0], "gratuity") for r in rows]
