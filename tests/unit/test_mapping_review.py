"""SPEC-05: low-confidence mappings are confirmed one at a time; changing a mapping applies it to
the loaded facts and checks their documents again with no ingest; a finding names the reviewed
mapping behind it; the checks that did not run are kept with what would make them run."""

import json
from pathlib import Path
from typing import Any

import pytest

from fin.actor import USER
from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.findings import format_finding, list_findings
from fin.map.dictionary import load_seed
from fin.map.review import read_pending
from fin.paths import audit_db_path, company_db_path, dictionary_db_path, review_dir
from fin.services import decisions
from fin.status import company_status, format_status

# A P&L whose "Sundry receipts" line is not in the dictionary: the user maps it.
LINES = [
    ("Revenue from operations", "revenue", "10000000.00"),
    ("Sundry receipts", None, "3000000.00"),
    ("Total income", "total_income", "13000000.00"),
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at, period_end) VALUES (1, 'ar.xlsx', 's', 1, "
            "'FY24', 'standalone', 'loaded', 'a', 'r', now(), DATE '2024-03-31')"
        )
        for row, (label, account, value) in enumerate(LINES, start=5):
            [(raw_id,)] = con.execute(
                "INSERT INTO raw_values (doc_id, sheet, cell, statement, section, raw_label, "
                "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (1, 'PL', ?, "
                "'profit_and_loss', '', ?, ?, '(Rs.)', 1, 'r') RETURNING raw_id",
                [f"C{row}", label, value],
            ).fetchall()
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY24', "
                "DATE '2024-03-31', 'standalone', ?, ?, 'r', 'FY24', 12)",
                [raw_id, account, value, account is not None],
            )
    return root


def pending_with(root: Path, confidences: list[float]) -> None:
    items = [
        {
            "id": n,
            "status": "proposed",
            "statement": "balance_sheet",
            "section": "",
            "label": f"Debtors {n}",
            "facts": 0,
            "where": [],
            "account": "trade_receivables",
            "confidence": confidence,
            "reason": "by name",
        }
        for n, confidence in enumerate(confidences, start=1)
    ]
    folder = review_dir(root, "acme")
    folder.mkdir(parents=True)
    pending = {"company": "acme", "next_id": len(items) + 1, "items": items}
    (folder / "pending.json").write_text(json.dumps(pending), encoding="utf-8")


def statuses(root: Path) -> dict[int, str]:
    return {i["id"]: i["status"] for i in read_pending(root, "acme")["items"]}


def commands(root: Path) -> list[str]:
    with connect(audit_db_path(root)) as con:
        rows = con.execute("SELECT command FROM runs WHERE record = 'end' ORDER BY started_at")
        return [c for (c,) in rows.fetchall()]


def confirm(root: Path, ids: list[int], each: bool = False) -> Any:
    return decisions.confirm_mappings(root, "acme", ids, via="cli", actor=USER, each=each)


def test_a_bulk_confirm_skips_low_confidence_ids_and_says_which(root: Path) -> None:
    pending_with(root, [0.9, 0.6, 0.8])
    done = confirm(root, [1, 2, 3])
    assert done.ok, done.lines
    assert "#1 confirmed (company scope); 0 facts updated" in done.lines
    assert "#3 confirmed (company scope); 0 facts updated" in done.lines
    assert (
        "#2 not confirmed: confidence 0.6 is below 0.8; confirm it on its own or with --each"
        in done.lines
    )
    assert statuses(root) == {2: "proposed"}  # confirmed items leave the review file


def test_only_low_ids_in_a_bulk_are_refused_and_alone_or_with_each_they_are_confirmed(
    root: Path,
) -> None:
    pending_with(root, [0.6, 0.5, 0.7])
    refused = confirm(root, [1, 2])
    assert (refused.ok, refused.code) == (False, "E304")
    assert statuses(root) == {1: "proposed", 2: "proposed", 3: "proposed"}
    assert confirm(root, [3]).ok  # named alone
    assert statuses(root) == {1: "proposed", 2: "proposed"}
    assert confirm(root, [1, 2], each=True).ok
    assert statuses(root) == {}


def mapping_id(root: Path) -> int:
    with connect(dictionary_db_path(root)) as con:
        [(found,)] = con.execute(
            "SELECT mapping_id FROM label_map WHERE source = 'map set'"
        ).fetchall()
    return int(found)


def test_changing_a_mapping_in_the_ui_clears_the_finding_it_caused_without_an_ingest(
    root: Path,
) -> None:
    wrong = decisions.set_mapping(
        root,
        "acme",
        ("profit_and_loss", "", "Sundry receipts"),
        "finance_cost",
        via="cli",
        actor=USER,
    )
    assert wrong.ok, wrong.lines
    run = Run("findings list", {}, root)
    [finding] = [f for f in list_findings(run, "acme") if f["status"] == "open"]
    assert finding["code"] == "E402"
    [named] = json.loads(finding["mappings"])
    assert (named["mapping_id"], named["label"], named["account"], named["source"]) == (
        mapping_id(root),
        "Sundry receipts",
        "finance_cost",
        "map set",
    )
    assert f"mapping #{mapping_id(root)} (map set)" in format_finding(finding)

    fixed = decisions.change_mapping(
        root, "acme", [mapping_id(root)], via="ui", actor=USER, account="other_income"
    )

    assert fixed.ok, fixed.lines
    assert "doc_id 1 ar.xlsx checked again: 0 open findings, loaded" in fixed.lines
    assert [f["status"] for f in list_findings(run, "acme")] == []
    assert "ingest" not in commands(root)
    assert commands(root).count("map set") == 2


def test_an_unknown_mapping_is_refused_with_e304(root: Path) -> None:
    refused = decisions.change_mapping(root, "acme", [99], via="ui", actor=USER, account="revenue")
    assert (refused.ok, refused.code) == (False, "E304")


def test_the_checks_that_did_not_run_are_kept_with_what_would_make_them_run(root: Path) -> None:
    decisions.set_mapping(
        root,
        "acme",
        ("profit_and_loss", "", "Sundry receipts"),
        "other_income",
        via="cli",
        actor=USER,
    )
    status = company_status(Run("status", {}, root), "acme")
    checks = {(n["check_name"], n["period"], n["scope"]) for n in status["checks_not_run"]}
    assert ("balance_sheet", "FY24", "standalone") in checks
    [balance] = [n for n in status["checks_not_run"] if n["check_name"] == "balance_sheet"]
    assert balance["needs"].startswith("confirm a ")
    assert "for FY24 standalone: map its line (Mappings)" in balance["needs"]
    assert "balance_sheet FY24 standalone did not run" in format_status([status])
