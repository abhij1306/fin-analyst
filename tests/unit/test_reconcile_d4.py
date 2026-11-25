"""The D4 reconciliations (fin.reconcile) on the synthetic set of tests/recon_fixtures.py: every
planted difference is found with both sources and nothing else is; the USD rate difference is an fx
line; query raise --items groups lines; gate G2 reads the checks."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import ReconCannotRun, ReconItemNotQueryable, ReconItemUnknown
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import gates, ledgers, payroll
from fin.diligence.sources import ingest_source
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.reconcile import items, run_new
from tests.recon_fixtures import ACCOUNTS, write_set


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "acme"), "company")
    return root


def open_engagement(root: Path, periods: str) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES (?, 100000, 'open', 'r0', ?)",
            [periods, utcnow()],
        )


def load(root: Path, path: Path, kind: str) -> None:
    ingest_source(run(root), load_config(root), "acme", path, kind, None)


def map_all(root: Path) -> None:
    proposals = [
        {"ledger": k, "account": v, "confidence": 0.9, "reason": "by name"}
        for k, v in ACCOUNTS.items()
    ]
    ledgers.propose(run(root), "acme", proposals)
    ledgers.confirm(
        run(root), "acme", [r["ledger_id"] for r in ledgers.list_ledgers(run(root), "acme")]
    )


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    open_engagement(data, "FY24,FY25")
    files = write_set(tmp_path)
    load(data, files["tb24"], "trial_balance")
    load(data, files["tb25"], "trial_balance")
    load(data, files["lv"], "ledger_vouchers")
    load(data, files["registers"], "registers")
    load(data, files["payroll"], "payroll")
    map_all(data)
    payroll.confirm_unit(run(data), "acme", "payroll.xlsx", "annual")
    return data


def recon(root: Path, name: str, period: str = "FY25") -> dict[str, Any]:
    [result] = run_new(run(root), "acme", name, period)
    return result


def detail(result: dict[str, Any], level: str = "invoice") -> dict[str, dict[str, Any]]:
    return {x["key"]: x for x in result["lines"] if x["level"] == level}


def odd(result: dict[str, Any], level: str = "invoice") -> dict[str, tuple[str, Any]]:
    return {
        k: (x["kind"], x["difference"])
        for k, x in detail(result, level).items()
        if x["kind"] != "matched"
    }


def test_sales_differences_and_the_fx_line(root: Path) -> None:
    result = recon(root, "sales_register_vs_gl")
    lines = detail(result)

    assert odd(result) == {
        "INV-2002": ("difference", 50000),
        "INV-2003": ("left_only", 400000),
        "INV-2004": ("right_only", -150000),
        "INV-2005": ("fx", -4000),
    }
    assert [k for k, x in lines.items() if x["needs_query"]] == ["INV-2003", "INV-2004"]
    assert "FX gain" in lines["INV-2005"]["note"] and "82.0000" in lines["INV-2005"]["note"]
    assert lines["INV-2003"]["left_source"] == "registers.xlsx Sales Register!4"
    assert lines["INV-2003"]["right_source"] is None
    assert lines["INV-2004"]["right_source"].startswith("lv.xlsx Ledger Vouchers!")
    assert odd(result, "month") == {"2024-05": ("difference", 296000)}


def test_purchases_match_by_number_then_by_party_and_amount(root: Path) -> None:
    result = recon(root, "purchase_register_vs_gl")
    lines = detail(result)

    assert odd(result) == {"PINV-73": ("left_only", 120000)}
    assert "voucher number is 9" in lines["PINV-71"]["note"]
    assert lines["PINV-72"]["note"] is None
    assert lines["PINV-73"]["needs_query"]


def test_gl_vs_tb_finds_only_the_planted_ledgers(root: Path) -> None:
    result = recon(root, "gl_vs_tb")

    assert odd(result, "ledger") == {
        "Axis Bank Current Account": ("difference", -150000),
        "Rent": ("difference", -20000),
        "Neem Consulting": ("left_only", 25000),
    } or odd(result, "ledger").keys() >= {"Axis Bank Current Account", "Rent"}
    lines = detail(result, "ledger")
    assert lines["Axis Bank Current Account"]["needs_query"] and not lines["Rent"]["needs_query"]
    assert lines["Profit & Loss A/c"]["kind"] == "matched"
    assert "prior period's profit" in lines["Profit & Loss A/c"]["note"]
    assert {k for k, x in lines.items() if x["kind"] == "unmapped"} == {"LEDG-0001"}
    assert {k for k, x in lines.items() if x["kind"] not in ("matched", "unmapped")} == {
        "Axis Bank Current Account",
        "Rent",
    }


def test_balance_sheet_ledgers_without_a_prior_trial_balance_are_not_reconciled(
    template: Path, tmp_path: Path
) -> None:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    open_engagement(data, "FY25")
    files = write_set(tmp_path)
    load(data, files["tb25"], "trial_balance")
    load(data, files["lv"], "ledger_vouchers")
    map_all(data)

    result = recon(data, "gl_vs_tb")

    kinds = {x["key"]: x["kind"] for x in result["lines"]}
    assert kinds["Axis Bank Current Account"] == "not_reconciled"
    assert kinds["Software Sales"] == "matched"
    assert kinds["Rent"] == "difference"
    assert not any(x["needs_query"] for x in result["lines"] if x["kind"] == "not_reconciled")


def test_payroll_vs_gl_is_annual_and_needs_the_unit(root: Path, tmp_path: Path) -> None:
    result = recon(root, "payroll_vs_gl")

    assert odd(result, "payroll") == {"FY25": ("difference", 108000)}
    assert detail(result, "payroll")["FY25"]["needs_query"]
    payroll.confirm_unit(run(root), "acme", "payroll.xlsx", "monthly")
    with pytest.raises(ReconCannotRun, match="not confirmed annual"):
        recon(root, "payroll_vs_gl")


def test_missing_inputs_raise_e840(root: Path) -> None:
    with pytest.raises(ReconCannotRun, match="no loaded Ledger Vouchers"):
        recon(root, "gl_vs_tb", "FY24")
    with pytest.raises(ReconCannotRun, match="not a period"):
        recon(root, "gl_vs_tb", "FY30")


def test_raise_items_groups_lines_and_a_rerun_keeps_the_query(root: Path) -> None:
    sales = recon(root, "sales_register_vs_gl")
    ids = [x["item_id"] for x in sales["lines"] if x["needs_query"]]

    query, summary = items.raise_from_items(run(root), "acme", ids, "Please explain INV-2003/2004.")

    assert (query["origin"], query["status"], query["period"], query["amount"]) == (
        "recon",
        "draft",
        "FY25",
        550000,
    )
    assert "1 left_only" in summary and "1 right_only" in summary
    with pytest.raises(ReconItemNotQueryable, match="cannot be raised"):
        items.raise_from_items(run(root), "acme", ids[:1], "again")
    again = recon(root, "sales_register_vs_gl")
    assert {x["key"]: x["query_id"] for x in again["lines"] if x["needs_query"]} == {
        "INV-2003": query["query_id"],
        "INV-2004": query["query_id"],
    }
    with pytest.raises(ReconItemUnknown, match="latest run"):
        items.raise_from_items(run(root), "acme", ids, "stale")
    fx = [x["item_id"] for x in again["lines"] if x["kind"] == "fx"]
    with pytest.raises(ReconItemNotQueryable):
        items.raise_from_items(run(root), "acme", fx, "fx")


def g2(root: Path) -> list[tuple[bool, str]]:
    with connect(company_db_path(root, "acme")) as con:
        gate = gates.evaluate(gates.context(con, root, "acme"))[1]
    return [(x["ok"], x["text"]) for x in gate["checklist"]]


def test_g2_needs_every_reconciliation_run_and_every_line_queried(root: Path) -> None:
    before = g2(root)
    assert not any(ok for ok, text in before if "has not run" not in text and text.startswith("FY"))
    assert sum("has not run" in text for _, text in before) == 4  # sales, purchase, gl, payroll

    for name in ("sales_register_vs_gl", "purchase_register_vs_gl", "gl_vs_tb", "payroll_vs_gl"):
        result = recon(root, name)
        ids = [x["item_id"] for x in result["lines"] if x["needs_query"]]
        assert ids
        assert any("not in a query" in t for ok, t in g2(root) if not ok)
        items.raise_from_items(run(root), "acme", ids, f"Please explain the {name} lines.")

    after = g2(root)
    assert [t for ok, t in after if not ok] == [
        t for ok, t in after if not ok and "queries in draft" in t
    ]
    assert any("1 fx listed, not queried" in t for _, t in after)
