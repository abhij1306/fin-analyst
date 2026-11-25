"""FDD F3 (docs/fdd.md): receivables ageing against the doubtful debts provision, indicative
gratuity against the gratuity provision, the provision ledgers' review flow, the cut-off
reconciliations, the [fdd] config table and the golden kinds, on a small synthetic company."""

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import FinError
from fin.audit.logger import Run
from fin.cli.provisions import format_show
from fin.config import DEFAULT_CONFIG_TOML, FDD_DEFAULTS, load_config, write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import provisions
from fin.diligence.engagement import open_engagement, open_engagement_row
from fin.evals import provisions as provision_evals
from fin.evals.golden import Check
from fin.evals.score import fin_value
from fin.paths import company_db_path
from fin.reconcile import RECONCILIATIONS, checks, cutoff, run_new
from fin.reconcile.format import format_result
from tests.fdd_bsheet_fixtures import seed

D = Decimal


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("f3") / "data"
    data.mkdir()
    write_default_config(data)
    ensure_database(company_db_path(data, "acme"), "company")
    with connect(company_db_path(data, "acme"), write=True) as con:
        seed(con)
    return data


@pytest.fixture
def root(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    monkeypatch.setenv("FIN_DATA_DIR", str(data))
    return data


def confirm_both(root: Path) -> None:
    ids = provisions.propose(
        run(root),
        "acme",
        [
            {"ledger": "Provision for Doubtful Debts", "kind": "doubtful_debts", "reason": "name"},
            {"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "name"},
        ],
    )
    provisions.decide(run(root), "acme", ids, "confirmed")


def query(root: Path, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql, args or []).fetchall()


def test_receivables_are_aged_at_each_year_end_from_the_latest_bank_runs(root: Path) -> None:
    confirm_both(root)
    got = query(
        root,
        "SELECT period, bucket, open_inr, invoices, share, provision_inr, coverage, "
        "not_in_bank_run_inr FROM receivable_provision",
    )
    assert got == [
        ("FY24", "0-90", D("150000"), 2, D("0.652174"), D("46000"), D("0.2"), D("0")),
        ("FY24", "91-180", D("0"), 0, D("0"), D("46000"), D("0.575"), D("0")),
        ("FY24", "181-365", D("80000"), 1, D("0.347826"), D("46000"), D("0.575"), D("0")),
        ("FY24", "over 365", D("0"), 0, D("0"), D("46000"), None, D("0")),
        ("FY24", "total", D("230000"), 3, D("1"), D("46000"), D("0.2"), D("0")),
        ("FY25", "0-90", D("0"), 0, D("0"), D("126000"), D("0.2"), D("30000")),
        ("FY25", "91-180", D("300000"), 1, D("0.476190"), D("126000"), D("0.2"), D("30000")),
        ("FY25", "181-365", D("200000"), 1, D("0.317460"), D("126000"), D("0.381818"), D("30000")),
        ("FY25", "over 365", D("130000"), 2, D("0.206349"), D("126000"), D("0.969231"), D("30000")),
        ("FY25", "total", D("630000"), 4, D("1"), D("126000"), D("0.2"), D("30000")),
    ]
    # INV-1 is unpaid in the stale FY24 run but matched (paid 2024-05-10) in the latest one.
    assert query(root, 'SELECT period, open_inr FROM receivable_open_lines WHERE "row" = 2') == [
        ("FY24", D("100000"))
    ]


def test_instalments_received_by_the_year_end_reduce_what_is_open(root: Path) -> None:
    # INV-14: 100,000 paid in two receipts, 40,000 before the FY24 year end and 60,000 after it.
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO recon_items (recon_id, item_no, kind, level, key, difference) "
            "VALUES (102, 9, 'matched', 'match', 'INV-14', 0)"
        )
        for side, fid, row, day, amount in (
            ("invoice", 1, 9, date(2024, 3, 1), 100000),
            ("bank", 9, 8, date(2024, 3, 20), 40000),
            ("bank", 9, 9, date(2024, 4, 20), 60000),
        ):
            con.execute(
                "INSERT INTO recon_match_lines VALUES (102, 9, ?, ?, 'S', ?, 'CUST-0001', ?, "
                "'INV-14', 'INR', ?, ?)",
                [side, fid, row, day, amount, amount],
            )
    got = query(
        root,
        'SELECT period, open_inr, bucket FROM receivable_open_lines WHERE "row" = 9 ORDER BY 1',
    )
    assert got == [("FY24", D("60000"), "0-90")]
    # A receipt is not shared over an invoice of the same group dated after the year end; at the
    # next year end the group's unpaid 50,000 is shared over both invoices by amount.
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO recon_match_lines VALUES (102, 9, 'invoice', 1, 'S', 10, 'CUST-0001', "
            "DATE '2024-04-10', 'INV-15', 'INR', 50000, 50000)"
        )
    assert query(
        root, 'SELECT period, open_inr FROM receivable_open_lines WHERE "row" = 9 ORDER BY 1'
    ) == [("FY24", D("60000")), ("FY25", D("33333.33"))]


def test_no_provision_figure_until_a_ledger_is_confirmed(root: Path) -> None:
    got = query(root, "SELECT DISTINCT provision_inr, coverage FROM receivable_provision")
    assert got == [(None, None)]
    provisions.propose(
        run(root),
        "acme",
        [{"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "by name"}],
    )
    assert query(root, "SELECT DISTINCT provision_inr FROM provision_balances") == [(None,)]


def test_indicative_gratuity_per_employee_and_against_the_provision(root: Path) -> None:
    confirm_both(root)
    with connect(company_db_path(root, "acme")) as con:
        data = provisions.collect(con, load_config(root).fdd)
        lines = con.execute(
            "SELECT employee_code, years, status, gratuity_basic_share, gratuity_wage_code "
            "FROM gratuity_lines(0.5, 5, 2000000) ORDER BY employee_code"
        ).fetchall()
    assert lines == [
        ("E1", 9, "eligible", D("259615.38"), D("432692.31")),
        ("E2", 3, "under_min_years", None, None),
        ("E3", None, "incomplete", None, None),
        ("E4", 35, "eligible", D("2000000.00"), D("2000000.00")),
        ("E6", 5, "eligible", D("28846.15"), D("36057.69")),
    ]
    [g] = data["gratuity"]
    assert (g["period"], g["employees"], g["eligible"], g["incomplete"]) == ("FY25", 5, 3, 1)
    assert (g["gratuity_basic_share"], g["gratuity_wage_code"]) == (
        D("2288461.53"),
        D("2468750.00"),
    )
    assert g["provision_inr"] == D("500000")
    assert (g["shortfall_basic_share"], g["shortfall_wage_code"]) == (
        D("1788461.53"),
        D("1968750.00"),
    )
    assert data["assumptions"]["label"] == "indicative, undiscounted, not an actuarial valuation"
    text = format_show(data)
    assert "indicative, undiscounted, not an actuarial valuation" in text
    assert "wages = gross salary x 0.5" in text


def test_the_basic_share_assumption_comes_from_config(root: Path) -> None:
    fdd = {**FDD_DEFAULTS, "basic_share": D("0.4")}
    with connect(company_db_path(root, "acme")) as con:
        [g] = provisions.collect(con, fdd)["gratuity"]
    # E1: 1,200,000 x 0.4 x 15 x 9 / 312
    assert g["gratuity_basic_share"] == D("207692.31") + D("2000000") + D("23076.92")


def test_provision_ledger_proposals_are_checked_and_only_the_user_decides(root: Path) -> None:
    def refused(proposals: Any) -> str:
        current = run(root)
        with pytest.raises(FinError) as caught:
            provisions.propose(current, "acme", proposals)
        assert caught.value.code == "E920"
        return caught.value.message

    assert "not a ledger of a loaded trial balance" in refused(
        [{"ledger": "Nope", "kind": "gratuity", "reason": "x"}]
    )
    assert "kind must be one of" in refused(
        [{"ledger": "Gratuity Payable", "kind": "leave", "reason": "x"}]
    )
    assert "reason" in refused([{"ledger": "Gratuity Payable", "kind": "gratuity", "reason": " "}])
    assert "JSON list" in refused({})
    twice = {"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "x"}
    assert "proposed twice" in refused([twice, twice])
    [pid] = provisions.propose(
        run(root), "acme", [{"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "x"}]
    )
    [row] = provisions.decide(run(root), "acme", [pid], "confirmed")
    assert row["status"] == "confirmed"
    assert "is confirmed" in refused(
        [{"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "x"}]
    )
    for ids, status in (([pid], "confirmed"), ([99], "rejected")):
        current = run(root)
        with pytest.raises(FinError) as caught:
            provisions.decide(current, "acme", ids, status)
        assert caught.value.code == "E921"
    [row] = provisions.decide(run(root), "acme", [pid], "rejected")
    assert row["status"] == "rejected"
    # A rejected ledger can be proposed again; it keeps its id.
    assert provisions.propose(
        run(root), "acme", [{"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "y"}]
    ) == [pid]


def test_show_needs_an_engagement_period(root: Path) -> None:
    current = run(root)
    with pytest.raises(FinError) as caught:
        provisions.show(current, "acme", "FY23")
    assert caught.value.code == "E923"
    assert [r["bucket"] for r in provisions.show(run(root), "acme", "FY25")["receivables"]][-1] == (
        "total"
    )


def test_sales_cutoff_flags_invoices_whose_service_is_across_the_year_end(root: Path) -> None:
    [result] = run_new(run(root), "acme", cutoff.SALES, "FY24")
    lines = {x["key"]: x for x in result["lines"]}
    assert {k: (x["kind"], x["level"], x["difference"]) for k, x in lines.items()} == {
        "INV-1": ("left_only", "invoice", D("100000")),
        "INV-2": ("left_only", "invoice", D("200000")),
        "no service start": ("not_reconciled", "coverage", D("30000")),
    }
    assert lines["INV-1"]["needs_query"]
    assert lines["INV-2"]["needs_query"]
    assert "billed ahead" in lines["INV-1"]["note"]
    assert "billed late" in lines["INV-2"]["note"]
    assert lines["INV-1"]["left_source"] == "reg.xlsx Sales!2"
    assert lines["no service start"]["left_amount"] == D("30000")
    assert not lines["no service start"]["needs_query"]
    text = format_result(result)
    assert "1 not reconciled" in text.splitlines()[0]
    assert "not_reconciled coverage no service start (listed, not queried)" in text


def test_purchase_cutoff_flags_possible_unrecorded_liabilities(root: Path) -> None:
    [result] = run_new(run(root), "acme", cutoff.PURCHASE, "FY24")
    assert [(x["key"], x["difference"]) for x in result["lines"]] == [("P-1", D("150000"))]
    assert "within 60 days after the year end 2024-03-31" in result["lines"][0]["note"]


def test_a_purchase_number_two_vendors_use_is_two_lines(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            'INSERT INTO register_lines (file_id, sheet, "row", kind, invoice_no, vendor, '
            "invoice_date, service_start, amount_inr, run_id) VALUES (1, 'Purchase', 9, "
            "'purchase', 'P-1', 'VEND-0002', DATE '2024-04-30', DATE '2024-03-10', 5000, 'r0')"
        )
        con.execute("UPDATE register_lines SET vendor = 'VEND-0001' WHERE invoice_no = 'P-1'")
        con.execute("UPDATE register_lines SET vendor = 'VEND-0002' WHERE \"row\" = 9")
    [result] = run_new(run(root), "acme", cutoff.PURCHASE, "FY24")
    assert sorted((x["key"], x["difference"]) for x in result["lines"]) == [
        ("P-1 (VEND-0001)", D("150000")),
        ("P-1 (VEND-0002)", D("5000")),
    ]


def test_cutoff_windows_come_from_the_engagement_and_say_what_was_not_tested(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET unrecorded_days = 90")
    [result] = run_new(run(root), "acme", cutoff.PURCHASE, "FY24")
    keys = [x["key"] for x in result["lines"]]
    assert keys == ["P-1", "P-2", "register end"]
    assert "end on 2024-06-15, before 2024-06-29" in result["lines"][-1]["note"]


def test_an_engagement_copies_the_cutoff_windows_from_config(root: Path) -> None:
    text = DEFAULT_CONFIG_TOML.replace("cutoff_days = 15", "cutoff_days = 20")
    (root / "config.toml").write_text(text, encoding="utf-8")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET status = 'closed'")
    row = open_engagement(run(root), "acme", ["FY24", "FY25"], None)
    assert (row["cutoff_days"], row["unrecorded_days"]) == (20, 60)


def test_cutoff_applies_where_the_registers_reach_the_window(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con:
        engagement = open_engagement_row(con)
        assert engagement is not None
        required = checks.required(con, engagement)
    assert (cutoff.SALES, "FY24") in required
    assert (cutoff.PURCHASE, "FY24") in required
    assert (cutoff.SALES, "FY25") not in required
    assert cutoff.SALES in RECONCILIATIONS
    current = run(root)
    with pytest.raises(FinError) as caught:
        run_new(current, "acme", cutoff.SALES, "FY25")
    assert caught.value.code == "E922"


def test_golden_kinds_read_the_f3_views(root: Path) -> None:
    confirm_both(root)
    run_new(run(root), "acme", cutoff.SALES, "FY24")

    def value(kind: str, period: str, ledger: str = "") -> Decimal | None:
        check = Check(2, kind, period, "", "", ledger, "", "", "", False)
        with connect(company_db_path(root, "acme")) as con:
            return fin_value(con, check)

    assert value("receivable_ageing", "FY25", "over 365") == D("130000")
    assert value("doubtful_debt_provision", "FY24") == D("46000")
    assert value("gratuity_provision", "FY25") == D("500000")
    assert value("sales_cutoff", "FY24", "INV-2") == D("200000")
    with connect(company_db_path(root, "acme")) as con:
        rows = provision_evals.candidates(con, lambda kind, period, source, **v: (kind, period, v))
    assert ("gratuity_provision", "FY25", {"unit": "rupees"}) in rows
    assert sum(1 for kind, _, _ in rows if kind == "receivable_ageing") == 10


def test_the_fdd_table_is_optional_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    start, end = DEFAULT_CONFIG_TOML.index("[fdd]"), DEFAULT_CONFIG_TOML.index("[pdf]")
    path.write_text(DEFAULT_CONFIG_TOML[:start] + DEFAULT_CONFIG_TOML[end:], encoding="utf-8")
    assert load_config(tmp_path).fdd == FDD_DEFAULTS
    for old, new, problem in (
        ("basic_share = 0.5", "basic_share = 1.5", "fdd.basic_share = Decimal('1.5')"),
        ("cutoff_days = 15", "cutoff_days = -1", "fdd.cutoff_days = -1"),
        ("cutoff_days = 15", "cutoff = 15", "[fdd] has unknown key(s) cutoff"),
    ):
        path.write_text(DEFAULT_CONFIG_TOML.replace(old, new), encoding="utf-8")
        with pytest.raises(FinError) as caught:
            load_config(tmp_path)
        assert caught.value.code == "E002"
        assert problem in caught.value.message
