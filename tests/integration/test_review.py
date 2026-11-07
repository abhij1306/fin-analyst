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


# --- New accounts: proposed in `fin map propose`, added only on `fin review confirm`. ---


def new_account(
    account: str = "long_overdue_debtors",
    parent: str | None = "total_current_assets",
    **change: Any,
) -> dict[str, Any]:
    definition = {"parent": parent, "sign": 1, "unit": "rupees", "description": "Old debtors"}
    return {**proposal(account), "new_account": {**definition, **change}}


def dictionary(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(root / "shared" / "dictionary.duckdb") as con:
        return con.execute(sql).fetchall()


def last_run(audit_query: Query, command: str) -> str:
    return audit_query(
        "SELECT run_id FROM runs WHERE command = ? ORDER BY started_at DESC LIMIT 1", [command]
    )[0][0]


def test_new_account_is_added_on_confirm_and_validates_under_its_parent(
    root: Path, audit_query: Query
) -> None:
    result = fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")
    assert result.exit_code == 0, result.output
    assert dictionary(root, "SELECT * FROM accounts WHERE account = 'long_overdue_debtors'") == []
    listed = fin("review", "list", "-c", "bluepeak").stdout
    assert "NEW ACCOUNT long_overdue_debtors: + total_current_assets, rupees, 'Old debtors'" in (
        listed
    )

    result = fin("review", "confirm", "1", "-c", "bluepeak", "--scope", "global")

    assert result.exit_code == 0, result.output
    assert "#1 confirmed (global scope); 2 facts updated" in result.stdout
    assert dictionary(
        root,
        "SELECT account, statement, parent, sign, unit, description, source, run_id IS NOT NULL "
        "FROM accounts WHERE account = 'long_overdue_debtors'",
    ) == [
        (
            "long_overdue_debtors",
            "balance_sheet",
            "total_current_assets",
            1,
            "rupees",
            "Old debtors",
            "review",
            True,
        )
    ]
    assert [(f[1], f[2]) for f in facts(root)] == [("long_overdue_debtors", True)] * 2
    assert audit_query("SELECT decision, account, scope FROM mapping_decisions")[1:] == [
        ("confirmed", "long_overdue_debtors", "global")
    ]
    accounts = fin("map", "accounts", "-s", "balance_sheet").stdout
    assert [line.split()[0] for line in accounts.splitlines() if "[added]" in line] == [
        "long_overdue_debtors"
    ]

    # Ingested again, the label maps to the new account, so the subtotal check (E402) runs and adds
    # it into total current assets, which this document does not show, then into total assets.
    again = fin("ingest", "bluepeak", str(root / "edited.xlsx"), "--force")

    assert again.exit_code == 0, again.output
    assert "0 unmapped labels" in again.stdout
    assert audit_query(
        "SELECT fiscal_year, passed FROM validation_results WHERE run_id = ? "
        "AND check_name = 'subtotal' AND message LIKE '% standalone: total_assets vs%' ORDER BY 1",
        [last_run(audit_query, "ingest")],
    ) == [("FY24", True), ("FY25", True)]


def test_new_account_under_the_wrong_parent_fails_e402(root: Path) -> None:
    fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")

    result = fin(
        "review",
        "confirm",
        "1",
        "-c",
        "bluepeak",
        "--name",
        "old_debtors",
        "--parent",
        "total_equity",
    )

    assert result.exit_code == 0, result.output
    assert dictionary(root, "SELECT account, parent FROM accounts WHERE source = 'review'") == [
        ("old_debtors", "total_equity")
    ]
    again = fin("ingest", "bluepeak", str(root / "edited.xlsx"), "--force")
    assert again.exit_code == 1
    assert "E402" in again.output


def test_rejected_new_account_adds_nothing(root: Path) -> None:
    fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")

    result = fin("review", "reject", "1", "-c", "bluepeak")

    assert result.exit_code == 0, result.output
    assert dictionary(root, "SELECT account FROM accounts WHERE source = 'review'") == []
    assert dictionary(root, "SELECT label FROM label_map WHERE source = 'review'") == []
    assert [(f[1], f[2]) for f in facts(root)] == [(None, False)] * 2


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (
            new_account(parent="no_such_total"),
            "E304: new account 'long_overdue_debtors': parent 'no_such_total' is not an account "
            "of the same statement",
        ),
        (
            new_account(parent="pat"),
            "E304: new account 'long_overdue_debtors': parent 'pat' is not an account of the "
            "same statement",
        ),
        (
            new_account("trade_receivables"),
            "E304: new account 'trade_receivables' is already in the dictionary; propose it as "
            "an existing account",
        ),
        (
            new_account("Old Debtors"),
            "E304: new account 'Old Debtors': use a snake_case name, e.g. loans_to_nbfcs",
        ),
        (
            new_account(sign=None),
            "E304: new account 'long_overdue_debtors': sign must be 1 or -1 with a parent, "
            "empty without",
        ),
    ],
)
def test_invalid_new_account_is_e304_and_nothing_is_stored(
    root: Path, bad: dict[str, Any], message: str
) -> None:
    result = fin("map", "propose", propose_file(root, bad), "-c", "bluepeak")

    assert result.exit_code == 2
    assert result.stderr.splitlines()[0] == message
    assert pending(root)[0]["status"] == "pending"


def test_confirm_with_an_invalid_parent_adds_nothing(root: Path) -> None:
    fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")

    result = fin("review", "confirm", "1", "-c", "bluepeak", "--parent", "revenue")

    assert result.exit_code == 2
    assert result.stderr.splitlines()[0] == (
        "E304: new account 'long_overdue_debtors': parent 'revenue' is not an account of the "
        "same statement"
    )
    assert dictionary(root, "SELECT account FROM accounts WHERE source = 'review'") == []
    assert dictionary(root, "SELECT label FROM label_map WHERE source = 'review'") == []
    assert pending(root)[0]["status"] == "proposed"


def test_map_set_can_target_an_added_account(root: Path) -> None:
    fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")
    fin("review", "confirm", "1", "-c", "bluepeak")

    result = fin(
        "map", "set", "-c", "bluepeak", "-s", "balance_sheet", "-l", "Old dues", "-a",
        "long_overdue_debtors",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert result.stdout == "bluepeak: balance_sheet 'Old dues' -> long_overdue_debtors (added)\n"


def test_export_prints_added_accounts_and_global_labels_as_seed_rows(root: Path) -> None:
    fin("map", "propose", propose_file(root, new_account()), "-c", "bluepeak")
    fin("review", "confirm", "1", "-c", "bluepeak", "--scope", "global")

    result = fin("map", "accounts", "--added")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "# rows for templates/dictionary/schedule_iii.csv",
        "long_overdue_debtors,balance_sheet,total_current_assets,1,rupees,Old debtors",
        "# rows for templates/dictionary/labels.csv",
        "debtors outstanding over six months,balance_sheet,current assets loans and advances,"
        "long_overdue_debtors",
    ]
