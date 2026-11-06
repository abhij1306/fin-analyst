"""Mapping review: unmapped label -> pending.json -> propose -> confirm or reject."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import find, load_label_map
from fin.paths import company_db_path

runner = CliRunner()
LABEL = "Debtors outstanding over six months"
Query = Callable[..., list[tuple[Any, ...]]]


@pytest.fixture
def root(data_root: Path, samples: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An initialised data folder with Bluepeak FY25 loaded, its 'Sundry debtors' row renamed to a
    label the dictionary does not know (2 facts: FY25 and FY24)."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    assert runner.invoke(app, ["init"]).exit_code == 0
    book = load_workbook(samples / "clean" / "bluepeak" / "Bluepeak_Polymers_FY2024-25.xlsx")
    book["Standalone BS"]["A23"] = LABEL
    path = data_root / "edited.xlsx"
    book.save(path)
    assert runner.invoke(app, ["ingest", "bluepeak", str(path)]).exit_code == 0
    return data_root


def fin(*args: str, input: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=input)


def pending(root: Path) -> list[dict[str, Any]]:
    path = root / "review" / "bluepeak" / "pending.json"
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def propose_file(root: Path, *proposals: dict[str, Any]) -> str:
    path = root / "proposals.json"
    path.write_text(json.dumps(list(proposals)), encoding="utf-8")
    return str(path)


def proposal(account: str = "trade_receivables", confidence: float = 0.9) -> dict[str, Any]:
    return {"label": LABEL, "account": account, "confidence": confidence, "reason": "same meaning"}


def facts(root: Path) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "bluepeak")) as con:
        return con.execute(
            "SELECT r.cell, f.account, f.confirmed, f.mapping_id IS NOT NULL FROM facts f "
            "JOIN raw_values r USING (raw_id) WHERE r.raw_label = ? ORDER BY 1",
            [LABEL],
        ).fetchall()


def test_ingest_writes_the_pending_list(root: Path) -> None:
    assert pending(root) == [
        {
            "id": 1,
            "status": "pending",
            "statement": "balance_sheet",
            "section": pending(root)[0]["section"],
            "label": LABEL,
            "facts": 2,
            "where": ["edited.xlsx Standalone BS!B23", "edited.xlsx Standalone BS!C23"],
            "account": None,
            "confidence": None,
            "reason": None,
        }
    ]
    listed = json.loads(fin("review", "list", "--json").stdout)
    assert [(i["company"], i["id"], i["label"]) for i in listed] == [("bluepeak", 1, LABEL)]


def test_propose_then_confirm_grows_the_dictionary_and_confirms_the_facts(
    root: Path, audit_query: Query, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = fin("map", "propose", propose_file(root, proposal()), "--company", "bluepeak")
    assert result.exit_code == 0, result.output
    assert pending(root)[0]["status"] == "proposed"
    assert [(f[1], f[2]) for f in facts(root)] == [(None, False), (None, False)]  # not applied
    assert audit_query(
        "SELECT actor, decision, on_request, account, confidence FROM mapping_decisions"
    ) == [("claude", "proposed", False, "trade_receivables", 0.9)]

    section = pending(root)[0]["section"]
    monkeypatch.setenv("CLAUDECODE", "1")
    result = fin("review", "confirm", "1", "--company", "bluepeak", "--scope", "global")

    assert result.exit_code == 0, result.output
    assert [(f[1], f[2], f[3]) for f in facts(root)] == [("trade_receivables", True, True)] * 2
    mapping = find(load_label_map(root, "kestrel"), "balance_sheet", section, LABEL)
    assert mapping is not None and mapping.account == "trade_receivables"  # global: any company
    assert pending(root) == []
    assert audit_query(
        "SELECT company, raw_label, account, scope, decision, actor, on_request "
        "FROM mapping_decisions WHERE decision = 'confirmed'"
    ) == [("bluepeak", LABEL, "trade_receivables", "global", "confirmed", "claude", True)]


def test_confirm_by_a_person_with_another_account_is_an_edit(
    root: Path, audit_query: Query
) -> None:
    section = pending(root)[0]["section"]
    fin("map", "propose", propose_file(root, proposal("other_current_assets")), "-c", "bluepeak")

    result = fin("review", "confirm", "1", "-c", "bluepeak", "--account", "trade_receivables")

    assert result.exit_code == 0, result.output
    assert audit_query("SELECT decision, scope, actor, on_request FROM mapping_decisions")[1:] == [
        ("edited", "company", "human", False)
    ]
    assert {f[1] for f in facts(root)} == {"trade_receivables"}
    own = find(load_label_map(root, "bluepeak"), "balance_sheet", section, LABEL)
    assert own is not None and own.account == "trade_receivables"
    assert (
        find(load_label_map(root, "kestrel"), "balance_sheet", section, LABEL) is None
    )  # company scope


def test_reject_leaves_the_facts_unconfirmed(root: Path, audit_query: Query) -> None:
    fin("map", "propose", propose_file(root, proposal()), "-c", "bluepeak")

    result = fin("review", "reject", "1", "-c", "bluepeak")

    assert result.exit_code == 0, result.output
    assert [(f[1], f[2], f[3]) for f in facts(root)] == [(None, False, False)] * 2
    assert [i["status"] for i in pending(root)] == ["rejected"]
    assert "nothing to review" in fin("review", "list").stdout
    assert audit_query("SELECT decision, account FROM mapping_decisions")[1:] == [
        ("rejected", "trade_receivables")
    ]


def test_unknown_account_is_rejected_and_nothing_is_stored(root: Path) -> None:
    result = fin("map", "propose", propose_file(root, proposal("made_up")), "-c", "bluepeak")

    assert result.exit_code == 2
    assert "E304: unknown account 'made_up'" in result.stderr
    assert pending(root)[0]["status"] == "pending"


def test_account_of_another_statement_is_rejected(root: Path) -> None:
    result = fin("map", "propose", propose_file(root, proposal("revenue")), "-c", "bluepeak")

    assert result.exit_code == 2
    assert "E304" in result.stderr


def test_two_accounts_for_one_label_conflict(root: Path) -> None:
    both = propose_file(root, proposal("trade_receivables"), proposal("other_current_assets"))

    result = fin("map", "propose", both, "-c", "bluepeak")

    assert result.exit_code == 1
    assert "E303" in result.stderr
    assert pending(root)[0]["status"] == "pending"


def test_low_confidence_proposal_is_logged_as_e302(root: Path, audit_query: Query) -> None:
    result = fin("map", "propose", propose_file(root, proposal(confidence=0.4)), "-c", "bluepeak")

    assert result.exit_code == 0
    assert audit_query("SELECT level, error_code FROM events WHERE error_code = 'E302'") == [
        ("warning", "E302")
    ]


def test_confirm_without_any_account_is_an_error(root: Path) -> None:
    result = fin("review", "confirm", "1", "-c", "bluepeak")

    assert result.exit_code == 2
    assert "E304" in result.stderr
    assert facts(root)[0][1] is None


def test_interactive_review_confirms_with_an_edited_account(root: Path) -> None:
    # edit -> account -> scope; then the list is empty
    result = fin("review", input="e\ntrade_receivables\nglobal\n")

    assert result.exit_code == 0, result.output
    assert "confirmed as trade_receivables (global); 2 facts updated" in result.stdout
    assert {f[1] for f in facts(root)} == {"trade_receivables"}


def test_interactive_review_can_skip_and_reject(root: Path) -> None:
    assert fin("review", input="s\n").exit_code == 0
    assert pending(root)[0]["status"] == "pending"
    assert fin("review", input="r\n").exit_code == 0
    assert pending(root)[0]["status"] == "rejected"


def test_new_label_confirmed_once_is_mapped_on_the_next_ingest(root: Path, samples: Path) -> None:
    fin("map", "propose", propose_file(root, proposal()), "-c", "bluepeak")
    fin("review", "confirm", "1", "-c", "bluepeak")
    book = load_workbook(samples / "clean" / "bluepeak" / "Bluepeak_Polymers_FY2023-24.xlsx")
    book["Standalone BS"]["A23"] = LABEL
    path = root / "edited2.xlsx"
    book.save(path)

    result = fin("ingest", "bluepeak", str(path))

    assert "0 unmapped labels" in result.stdout
    assert (
        not (root / "review" / "bluepeak" / "pending.json")
        .read_text(encoding="utf-8")
        .count('"label"')
    )
