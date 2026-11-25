"""Pass-through customers and adjustment effects (fin.diligence.passthrough, .adjustments, and the
views revenue_split, passthrough_lines, adjusted_ebitda, ebitda_bridge) on a synthetic registers
workbook: a pass-through customer's revenue is split out of core revenue and growth, and a
reclassification changes the margin on adjusted revenue with no EBITDA effect."""

import csv
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.audit.errors import AdjustmentInvalid, PassthroughInvalid
from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence import adjustments, passthrough
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from tests.register_fixtures import sales_line, write_registers
from tests.tally_fixtures import CUSTOMERS

D = Decimal
FIGURES = [
    ("profit_and_loss", "revenue", 1000000),
    ("profit_and_loss", "employee_expense", 300000),
    ("profit_and_loss", "other_expenses", 500000),
    ("profit_and_loss", "pbt", 200000),
    ("profit_and_loss", "finance_cost", 0),
    ("profit_and_loss", "depreciation", 0),
    ("profit_and_loss", "other_income", 0),
]
# FY24 lines (dated May 2023): a core customer and the pass-through one (CUSTOMERS[0]).
FY24_EXTRA = [
    sales_line(5, CUSTOMERS[1], 50000, **{"Invoice Date": "10-05-2023"}),
    sales_line(6, CUSTOMERS[0], 40000, **{"Invoice Date": "11-05-2023"}),
]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with the dictionary, company acme with FY25 figures and an open engagement
    for FY24 and FY25 (made once)."""
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at) VALUES (1, 'fs.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'x', 'r0', now())"
        )
        for raw_id, (statement, account, value) in enumerate(FIGURES, start=1):
            con.execute(
                "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                "VALUES (?, 1, 'PL', ?, ?, '', ?, '', '', 1, 'r0')",
                [raw_id, f"C{raw_id}", statement, account],
            )
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
                [raw_id, account, value],
            )
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


@pytest.fixture
def bare(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


@pytest.fixture
def root(bare: Path, tmp_path: Path) -> Path:
    """The same, with the registers loaded."""
    path = write_registers(tmp_path / "reg.xlsx", sales_extra=FY24_EXTRA)
    run = Run("ingest", {}, bare)
    ingest_source(run, load_config(bare), "acme", path, "registers", None)
    return bare


def run(root: Path) -> Run:
    return Run("test", {}, root)


def query(root: Path, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        return fetch_dicts(con, sql, params or [])


def passthrough_token(root: Path) -> str:
    """The token of CUSTOMERS[0] (invoice INV-1001's customer)."""
    return query(root, "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1001'")[0][
        "customer"
    ]


def propose(root: Path, **kwargs: Any) -> dict[str, Any]:
    defaults = {
        "period": "FY25",
        "scope": "standalone",
        "source_ref": "reg.xlsx Sales!2",
        "category": "normalisation",
        "description": "d",
    }
    return adjustments.propose(run(root), "acme", **(defaults | kwargs))


def accept(root: Path, adj_id: int) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE adjustments SET status = 'accepted' WHERE adj_id = ?", [adj_id])


def test_a_list_needs_a_loaded_sales_register(bare: Path) -> None:
    with pytest.raises(PassthroughInvalid, match="no loaded sales register"):
        passthrough.set_customers(run(bare), "acme", ["CUST-0001"], "email")


def test_the_list_is_checked_against_the_sales_register(root: Path) -> None:
    with pytest.raises(PassthroughInvalid, match="CUST-9999"):
        passthrough.set_customers(run(root), "acme", ["CUST-9999"], "email")
    assert query(root, "SELECT * FROM passthrough_customers") == []


def test_setting_the_list_replaces_it_and_shows_each_customers_revenue(root: Path) -> None:
    token = passthrough_token(root)
    result = passthrough.set_customers(run(root), "acme", [token, token], "email 2026-11-25")

    assert [(c["customer"], c["sales_lines"], c["revenue"]) for c in result["customers"]] == [
        (token, 3, D("320000.00"))
    ]
    other = query(root, "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1002'")[0]
    assert (
        passthrough.set_customers(run(root), "acme", [other["customer"]], "x")["customers"][0][
            "customer"
        ]
        == other["customer"]
    )
    assert passthrough.set_customers(run(root), "acme", [], None)["customers"] == []
    assert "pass-through customers of engagement #1: none" in passthrough.format_show(
        passthrough.show(run(root), "acme")
    )


def test_revenue_split_separates_passthrough_from_core_and_growth(root: Path) -> None:
    token = passthrough_token(root)
    passthrough.set_customers(run(root), "acme", [token], "email")

    fy24, fy25 = query(root, "SELECT * FROM revenue_split ORDER BY period")
    assert (fy24["core_revenue"], fy24["passthrough_revenue"]) == (D("50000.00"), D("40000.00"))
    assert fy24["core_revenue_growth"] is None  # no earlier period in the engagement
    assert (fy25["sales_revenue"], fy25["core_revenue"], fy25["passthrough_revenue"]) == (
        D("360000.00"),
        D("80000.00"),
        D("280000.00"),
    )
    # Growth is on core revenue only: 80,000 / 50,000 - 1. All revenue would give 300%.
    assert fy25["core_revenue_growth"] == D("0.600000")
    assert (fy25["passthrough_licence_revenue"], fy25["passthrough_services_revenue"]) == (
        D("120000.00"),
        D("160000.00"),
    )
    assert fy25["linked_purchase_cost"] == D("90000.00")  # the purchase behind Purchase Id P1
    assert fy24["linked_purchase_cost"] == D("0.00")


def test_golden_checks_score_the_linked_purchase_cost(root: Path) -> None:
    passthrough.set_customers(run(root), "acme", [passthrough_token(root)], "email")
    init_golden(run(root), "acme")
    path = golden_path(root, "acme")
    with path.open(encoding="utf-8", newline="") as handle:
        listed = {(r["kind"], r["period"]) for r in csv.DictReader(handle)}
    assert {("linked_purchase_cost", "FY24"), ("linked_purchase_cost", "FY25")} <= listed
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for period, expected in (("FY25", "90000"), ("FY24", "5")):  # FY24 planted wrong
            writer.writerow(
                ("linked_purchase_cost", period, "", "", "", expected, "rupees", "s", "yes")
            )

    result = run_eval(run(root), "acme")

    assert [c["outcome"] for c in result["checks"]] == ["pass", "fail"]


def test_no_passthrough_customers_means_all_revenue_is_core(root: Path) -> None:
    fy24, fy25 = query(root, "SELECT * FROM revenue_split ORDER BY period")

    assert (fy25["core_revenue"], fy25["passthrough_revenue"]) == (D("360000.00"), D("0.00"))
    assert fy25["core_revenue_growth"] == D("3.000000")  # 360,000 / 90,000 - 1
    assert query(root, "SELECT * FROM passthrough_lines") == []


def test_passthrough_lines_link_to_the_oem_purchase(root: Path) -> None:
    passthrough.set_customers(run(root), "acme", [passthrough_token(root)], "email")

    lines = query(root, "SELECT * FROM passthrough_lines WHERE period = 'FY25' ORDER BY invoice_no")

    assert [
        (r["invoice_no"], r["link"], r["purchase_id"], r["purchase_amount_inr"]) for r in lines
    ] == [
        ("INV-1001", "licence", "P1", D("90000.00")),
        ("INV-1003", "services", None, None),
    ]
    assert lines[0]["purchase_sheet"] == "Purchase Register"
    assert lines[0]["vendor"].startswith("VEND-")


def test_effects_default_to_a_cost_effect_and_must_add_up(root: Path) -> None:
    alone = propose(root, amount=D("50000"))
    assert (alone["amount"], alone["revenue_effect"], alone["cost_effect"]) == (
        D("50000.00"),
        D("0.00"),
        D("-50000.00"),
    )
    strip = propose(root, revenue_effect=D("-280000"), cost_effect=D("-90000"))
    assert (strip["amount"], strip["revenue_effect"], strip["cost_effect"]) == (
        D("-190000.00"),
        D("-280000.00"),
        D("-90000.00"),
    )
    with pytest.raises(AdjustmentInvalid, match="not the revenue effect"):
        propose(root, amount=D("1"), revenue_effect=D("-280000"), cost_effect=D("-90000"))
    with pytest.raises(AdjustmentInvalid, match="give --amount"):
        propose(root)
    assert propose(root, amount=D("-190000"), revenue_effect=D("-280000"), cost_effect=D("-90000"))


def test_a_cost_reclassification_names_both_accounts_and_has_no_effect(root: Path) -> None:
    row = propose(
        root,
        category="reclassification",
        account="employee_expense",
        from_account="other_expenses",
        moved_amount=D("40000"),
        amount=D("0"),
    )
    assert (row["amount"], row["revenue_effect"], row["cost_effect"]) == (D("0.00"),) * 3
    assert "other_expenses -> employee_expense" in adjustments.format_adjustment(row)
    base = {"category": "reclassification", "account": "employee_expense", "amount": D("0")}
    for bad, match in (
        ({"from_account": "other_expenses"}, "go together"),
        ({"moved_amount": D("1")}, "go together"),
        ({"from_account": "revenue", "moved_amount": D("1")}, "must be a cost account"),
        ({"from_account": "depreciation", "moved_amount": D("1")}, "must be a cost account"),
        ({"from_account": "employee_expense", "moved_amount": D("1")}, "two different"),
        ({"from_account": "other_expenses", "moved_amount": D("-1")}, "positive"),
        (
            {"from_account": "other_expenses", "moved_amount": D("1"), "category": "error"},
            "reclass",
        ),
    ):
        with pytest.raises(AdjustmentInvalid, match=match):
            propose(root, **(base | bad))
    with pytest.raises(AdjustmentInvalid, match="has no revenue, cost or EBITDA effect"):
        propose(
            root,
            category="reclassification",
            account="employee_expense",
            from_account="other_expenses",
            moved_amount=D("1"),
            amount=D("5"),
        )


def test_a_reclassification_changes_margins_with_zero_ebitda_effect(root: Path) -> None:
    [before] = query(root, "SELECT * FROM adjusted_ebitda WHERE period = 'FY25'")
    assert (before["revenue"], before["reported_ebitda"]) == (D("1000000.00"), D("200000.00"))
    assert before["adjusted_revenue"] == D("1000000.00")
    # Pass-through revenue booked and expensed at the same amount, netted out of both.
    net = propose(
        root, category="reclassification", revenue_effect=D("-100000"), cost_effect=D("-100000")
    )
    assert net["amount"] == D("0.00")
    accept(root, net["adj_id"])

    [after] = query(root, "SELECT * FROM adjusted_ebitda WHERE period = 'FY25'")
    assert after["adjusted_ebitda"] == before["adjusted_ebitda"] == D("200000.00")
    assert after["adjusted_revenue"] == D("900000.00")
    assert before["adjusted_ebitda_margin"] == D("0.200000")
    assert after["adjusted_ebitda_margin"] == D("0.222222")  # on adjusted revenue
    assert after["reported_ebitda_margin"] == D("0.200000")
    assert (after["accepted_revenue_effect"], after["accepted_cost_effect"]) == (
        D("-100000.00"),
        D("-100000.00"),
    )


def test_a_passthrough_normalisation_strips_revenue_and_cost(root: Path) -> None:
    passthrough.set_customers(run(root), "acme", [passthrough_token(root)], "email")
    [fy25] = query(root, "SELECT * FROM revenue_split WHERE period = 'FY25'")
    strip = propose(
        root,
        revenue_effect=-fy25["passthrough_revenue"],
        cost_effect=-fy25["linked_purchase_cost"],
        description="Pass-through stripped",
    )
    accept(root, strip["adj_id"])

    [row] = query(root, "SELECT * FROM adjusted_ebitda WHERE period = 'FY25'")
    assert row["adjusted_revenue"] == D("720000.00")
    assert row["adjusted_ebitda"] == D("10000.00")  # 200,000 - (280,000 - 90,000)
    steps = query(root, "SELECT * FROM ebitda_bridge WHERE period = 'FY25' ORDER BY step")
    assert [(s["kind"], s["amount"]) for s in steps] == [
        ("reported", D("200000.00")),
        ("adjustment", D("-190000.00")),
        ("adjusted", D("10000.00")),
    ]
    assert (steps[1]["revenue_effect"], steps[1]["cost_effect"]) == (
        D("-280000.00"),
        D("-90000.00"),
    )
    assert steps[0]["revenue_effect"] is None


def test_the_commands(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    fin = CliRunner().invoke
    assert fin(app, ["init"]).exit_code == 0
    token = passthrough_token(root)

    assert fin(app, ["engagement", "passthrough", "acme", "--customer", token]).exit_code == 2
    unknown = fin(
        app, ["engagement", "passthrough", "acme", "--customer", "CUST-9999", "--source", "x"]
    )
    assert unknown.exit_code == 2
    assert unknown.stderr.startswith("E845: not a customer of acme's sales register: CUST-9999")
    result = fin(
        app, ["engagement", "passthrough", "acme", "--customer", token, "--source", "email"]
    )
    assert result.exit_code == 0, result.output
    assert f"{token}: 3 sales lines, ₹320,000.00 (source: email)" in result.stdout
    assert (
        "FY25: core ₹80,000.00, pass-through ₹280,000.00 (licence ₹120,000.00, services "
        "₹160,000.00), linked purchase cost ₹90,000.00, core growth 60.00%"
    ) in result.stdout

    proposed = fin(
        app,
        ["adjust", "propose", "acme", "--period", "FY25", "--scope", "standalone", "--category",
         "normalisation", "--revenue-effect", "-280000", "--cost-effect", "-90000",
         "--description", "Pass-through stripped", "--source", "revenue_split"],
    )  # fmt: skip
    assert proposed.exit_code == 0, proposed.output
    assert proposed.stdout == (
        "#1 proposed FY25 standalone normalisation -₹190,000.00: Pass-through stripped\n"
        "    evidence: source revenue_split\n"
        "    effects: revenue -₹280,000.00, costs -₹90,000.00, EBITDA -₹190,000.00\n"
    )
    assert fin(app, ["engagement", "passthrough", "acme", "--clear"]).exit_code == 0
