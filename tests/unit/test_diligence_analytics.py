"""D9 analytics (views in diligence_analytics.sql, fin.diligence.customers, fin.diligence.analytics,
the report section and the eval checks) on a synthetic registers workbook, against an answer key
worked out by hand (Fractions, rounded to paise and to six places): customers C0..C3 are Orchid
(INV-1001), Lotus (INV-1002), Cedar (INV-1007) and Maple (INV-1009)."""

import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.audit.errors import AnalyticsUnavailable, CustomerMappingInvalid
from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence import analytics, customers, passthrough
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from fin.report.customers import customers_section
from fin.report.data import customer_analytics
from tests.register_fixtures import sales_line, write_registers
from tests.tally_fixtures import CUSTOMERS

D = Decimal
CEDAR, MAPLE = "Cedar Retail Ltd", "Maple Logistics Pvt Ltd"
FY24 = {"Invoice Date": "10-05-2023"}


def line(n: int, customer: str, amount: int, qty: int, **over: object) -> list[object]:
    return sales_line(
        n, customer, amount, **{"Quantity": qty, "Rate": amount / qty, "Product": "Support", **over}
    )


# FY25 INV-1001..1003 come with write_registers (Orchid 120,000 "Licence for ...", Lotus 80,000
# Support, Orchid 160,000 Support, each quantity 2).
EXTRA = [
    line(5, CUSTOMERS[1], 50000, 1, **FY24),
    line(6, CUSTOMERS[0], 40000, 1, **FY24),
    line(7, CEDAR, 30000, 3, **FY24),
    line(8, CEDAR, 10000, 1),
    line(9, MAPLE, 50000, 5),
    line(10, MAPLE, 20000, 2, Product="Training"),
    line(11, CUSTOMERS[1], 25000, 5, Product="Training", **FY24),
    line(12, CEDAR, 15000, 3, Product="Audit", **FY24),
]
INVOICES = {"C0": "INV-1001", "C1": "INV-1002", "C2": "INV-1007", "C3": "INV-1009"}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("analytics") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
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
    path = write_registers(tmp_path / "reg.xlsx", sales_extra=EXTRA)
    ingest_source(Run("ingest", {}, bare), load_config(bare), "acme", path, "registers", None)
    return bare


def write_golden(root: Path, lines: list[tuple[str, ...]]) -> None:
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(lines)


def run(root: Path) -> Run:
    return Run("test", {}, root)


def query(root: Path, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        return fetch_dicts(con, sql, params or [])


def tokens(root: Path) -> dict[str, str]:
    """C0..C3 -> the customer tokens."""
    return {
        name: query(root, "SELECT customer FROM register_lines WHERE invoice_no = ?", [inv])[0][
            "customer"
        ]
        for name, inv in INVOICES.items()
    }


def sectors(root: Path) -> None:
    """Orchid and Lotus: Banking, Cedar: Retail; Maple stays unconfirmed."""
    t = tokens(root)
    proposals = [
        {"customer": t[c], "sector": s, "confidence": 0.8, "reason": "products"}
        for c, s in (("C0", "Banking"), ("C1", "Banking"), ("C2", "Retail"), ("C3", "Telecom"))
    ]
    customers.propose(run(root), "acme", proposals)
    ids = {r["customer"]: r["customer_id"] for r in query(root, "SELECT * FROM customers")}
    customers.confirm(run(root), "acme", [ids[t[c]] for c in ("C0", "C1", "C2")])


def test_concentration_matches_the_key(root: Path) -> None:
    rows = query(root, "SELECT * FROM customer_concentration ORDER BY period, segment")

    assert [
        (
            r["period"],
            r["segment"],
            r["customers"],
            r["revenue"],
            r["top1_revenue"],
            r["top1_share"],
        )
        for r in rows
    ] == [
        ("FY24", "core", 3, D("160000.00"), D("75000.00"), D("0.468750")),
        ("FY25", "core", 4, D("440000.00"), D("280000.00"), D("0.636364")),
    ]
    assert (rows[1]["top5_revenue"], rows[1]["top10_share"]) == (D("440000.00"), D("1.000000"))
    top = query(root, "SELECT customer, revenue, rank FROM customer_revenue WHERE period = 'FY25'")
    t = tokens(root)
    assert [(r["customer"], r["revenue"], r["rank"]) for r in top] == [
        (t["C0"], D("280000.00"), 1),
        (t["C1"], D("80000.00"), 2),
        (t["C3"], D("70000.00"), 3),
        (t["C2"], D("10000.00"), 4),
    ]


def test_passthrough_is_a_separate_segment_and_never_in_retention_or_pvm(root: Path) -> None:
    passthrough.set_customers(run(root), "acme", [tokens(root)["C0"]], "email")

    rows = query(root, "SELECT * FROM customer_concentration ORDER BY period, segment")

    assert [(r["period"], r["segment"], r["revenue"], r["top1_share"]) for r in rows] == [
        ("FY24", "core", D("120000.00"), D("0.625000")),
        ("FY24", "passthrough", D("40000.00"), D("1.000000")),
        ("FY25", "core", D("160000.00"), D("0.500000")),
        ("FY25", "passthrough", D("280000.00"), D("1.000000")),
    ]
    [ret] = query(root, "SELECT * FROM customer_retention")
    assert (ret["prior_revenue"], ret["retained_revenue"], ret["new_revenue"]) == (
        D("120000.00"),
        D("90000.00"),
        D("70000.00"),
    )
    assert (ret["net_retention"], ret["gross_retention"]) == (D("0.750000"), D("0.708333"))
    [pvm] = query(root, "SELECT * FROM product_pvm_summary")
    assert (pvm["price_effect"], pvm["volume_effect"], pvm["mix_effect"]) == (
        D("-10000.00"),
        D("65000.00"),
        D("0.00"),
    )
    assert (pvm["new_revenue"], pvm["discontinued_revenue"], pvm["change"]) == (
        D("0.00"),
        D("15000.00"),
        D("40000.00"),
    )
    pt_total = query(root, "SELECT passthrough_revenue FROM revenue_split WHERE period = 'FY25'")
    assert pt_total[0]["passthrough_revenue"] == D("280000.00")


def test_retention_and_cohorts_match_the_key(root: Path) -> None:
    [ret] = query(root, "SELECT * FROM customer_retention")

    assert (ret["period"], ret["prior_period"]) == ("FY25", "FY24")
    assert (ret["prior_customers"], ret["retained_customers"], ret["lost_customers"]) == (3, 3, 0)
    assert (ret["new_customers"], ret["new_revenue"]) == (1, D("70000.00"))
    assert (ret["prior_revenue"], ret["retained_revenue"]) == (D("160000.00"), D("370000.00"))
    assert (ret["expansion"], ret["contraction"]) == (D("245000.00"), D("35000.00"))
    assert (ret["net_retention"], ret["gross_retention"], ret["logo_retention"]) == (
        D("2.312500"),
        D("0.781250"),
        D("1.000000"),
    )
    # retained = prior - lost - contraction + expansion
    assert ret["retained_revenue"] == (
        ret["prior_revenue"] - ret["lost_revenue"] - ret["contraction"] + ret["expansion"]
    )
    cohorts = query(root, "SELECT * FROM customer_cohorts ORDER BY cohort, period")
    assert [
        (r["cohort"], r["opening"], r["period"], r["active_customers"], r["revenue"],
         r["logo_retention"], r["revenue_retention"])
        for r in cohorts
    ] == [
        ("FY24", True, "FY24", 3, D("160000.00"), D("1.000000"), D("1.000000")),
        ("FY24", True, "FY25", 3, D("370000.00"), D("1.000000"), D("2.312500")),
        ("FY25", False, "FY25", 1, D("70000.00"), D("1.000000"), D("1.000000")),
    ]  # fmt: skip


def test_price_volume_mix_matches_the_key(root: Path) -> None:
    rows = query(root, "SELECT * FROM product_pvm WHERE kind = 'continuing' ORDER BY product")

    got = {
        r["product"]: (
            r["prior_price"],
            r["price"],
            r["price_effect"],
            r["volume_effect"],
            r["mix_effect"],
        )
        for r in rows
    }
    assert got == {
        "Support": (D("24000.00"), D("30000.00"), D("60000.00"), D("86896.55"), D("33103.45")),
        "Training": (D("5000.00"), D("10000.00"), D("10000.00"), D("18103.45"), D("-33103.45")),
    }
    [total] = query(root, "SELECT * FROM product_pvm_summary")
    assert (total["price_effect"], total["volume_effect"], total["mix_effect"]) == (
        D("70000.00"),
        D("105000.00"),
        D("0.00"),
    )
    assert (total["new_revenue"], total["discontinued_revenue"]) == (D("120000.00"), D("15000.00"))
    # change = price + volume + mix + new - discontinued
    assert total["change"] == D("440000.00") - D("160000.00")
    assert total["change"] == (
        total["price_effect"] + total["volume_effect"] + total["mix_effect"]
        + total["new_revenue"] - total["discontinued_revenue"]
    )  # fmt: skip
    [new] = query(root, "SELECT product FROM product_pvm WHERE kind = 'new'")
    assert new["product"].startswith("Licence for ")
    [gone] = query(root, "SELECT product FROM product_pvm WHERE kind = 'discontinued'")
    assert gone["product"] == "Audit"


def test_sector_mix_counts_unconfirmed_customers_as_unmapped(root: Path) -> None:
    sectors(root)

    rows = query(root, "SELECT * FROM sector_mix WHERE period = 'FY25' ORDER BY revenue DESC")

    assert [(r["sector"], r["customers"], r["revenue"], r["share"]) for r in rows] == [
        ("Banking", 2, D("360000.00"), D("0.818182")),
        ("unmapped", 1, D("70000.00"), D("0.159091")),
        ("Retail", 1, D("10000.00"), D("0.022727")),
    ]
    assert query(
        root, "SELECT revenue FROM sector_mix WHERE period = 'FY24' AND sector = 'Retail'"
    )[0]["revenue"] == D("45000.00")


def proposal(customer: str, sector: str, confidence: float = 1, reason: str = "") -> dict[str, Any]:
    return {"customer": customer, "sector": sector, "confidence": confidence, "reason": reason}


def test_registers_load_adds_pending_customers_and_review_decides(root: Path) -> None:
    rows = query(root, "SELECT * FROM customers ORDER BY customer_id")
    assert [r["status"] for r in rows] == ["pending"] * 4
    t = tokens(root)
    r = run(root)
    unknown, blank, reuse = (
        proposal("CUST-9999", "x"),
        proposal(t["C0"], "Unmapped"),
        proposal(t["C0"], "x"),
    )

    with pytest.raises(CustomerMappingInvalid, match="not a customer"):
        customers.propose(r, "acme", [unknown])
    with pytest.raises(CustomerMappingInvalid, match="no proposed sector"):
        customers.confirm(r, "acme", [rows[0]["customer_id"]])
    with pytest.raises(CustomerMappingInvalid, match="unmapped"):
        customers.propose(r, "acme", [blank])
    with pytest.raises(CustomerMappingInvalid, match="no customer with id 99"):
        customers.reject(r, "acme", [99])
    assert {row["status"] for row in query(root, "SELECT * FROM customers")} == {"pending"}

    customers.propose(r, "acme", [proposal(t["C0"], " Banking ", 0.9, "r")])
    first = rows[0]["customer_id"]
    not_yet = query(root, "SELECT sector FROM sector_mix WHERE sector = 'Banking'")
    assert not_yet == []  # proposed, not confirmed
    [edited] = customers.confirm(r, "acme", [first], "Financial services")
    assert (edited["status"], edited["sector"]) == ("confirmed", "Financial services")
    with pytest.raises(CustomerMappingInvalid, match="only the user changes it"):
        customers.propose(r, "acme", [reuse])
    second = rows[1]["customer_id"]
    customers.propose(r, "acme", [proposal(rows[1]["customer"], "Retail", 0.5, "r")])
    [rejected] = customers.reject(r, "acme", [second])
    assert rejected["sector"] is None
    waiting = customers.list_customers(r, "acme", open_only=True)
    assert [row["status"] for row in waiting] == ["pending", "pending"]


def test_the_report_section_and_the_data_have_no_names(root: Path) -> None:
    sectors(root)
    with connect(company_db_path(root, "acme")) as con:
        data = {"customers": customer_analytics(con)}

    text = customers_section(data, "rupees")

    assert "## Customers, sectors and pricing" in text
    assert "63.6%" in text
    assert "Banking" in text
    assert "unmapped" in text
    assert "Orchid" not in text
    assert "Lotus" not in text
    assert customers_section({"customers": None}, "rupees") == ""


def test_a_cohort_with_nobody_left_keeps_its_rows(root: Path) -> None:
    """FY24 core customers are Lotus and Cedar once Orchid is pass-through and neither has FY25
    sales: the FY24 cohort is still listed in FY25, with zeros."""
    passthrough.set_customers(run(root), "acme", [tokens(root)["C0"]], "email")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("DELETE FROM register_lines WHERE invoice_no IN ('INV-1002', 'INV-1008')")

    rows = query(root, "SELECT * FROM customer_cohorts ORDER BY cohort, period")

    assert [
        (r["cohort"], r["period"], r["active_customers"], r["revenue"], r["revenue_retention"])
        for r in rows
    ] == [
        ("FY24", "FY24", 2, D("120000.00"), D("1.000000")),
        ("FY24", "FY25", 0, D("0.00"), D("0.000000")),
        ("FY25", "FY25", 1, D("70000.00"), D("1.000000")),
    ]


def test_analytics_needs_a_sales_register(bare: Path) -> None:
    run_bare = run(bare)
    with pytest.raises(AnalyticsUnavailable, match="no sales register lines"):
        analytics.collect(run_bare, "acme")


def test_the_commands(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    fin = CliRunner().invoke
    t = tokens(root)

    listed = fin(app, ["review", "list", "acme", "--customers"])
    assert listed.exit_code == 0, listed.output
    assert f"[pending] {t['C0']}: 3 sales lines" in listed.stdout

    proposals = root / "proposals.json"
    proposals.write_text(
        json.dumps(
            [{"customer": t["C0"], "sector": "Banking", "confidence": 0.9, "reason": "licences"}]
        ),
        encoding="utf-8",
    )
    stored = fin(app, ["map", "customers", "acme", str(proposals)])
    assert stored.exit_code == 0, stored.output
    cid = query(root, "SELECT customer_id FROM customers WHERE customer = ?", [t["C0"]])[0][
        "customer_id"
    ]
    bad = fin(app, ["review", "confirm", "acme", str(cid), "--customers", "--account", "revenue"])
    assert bad.exit_code != 0
    done = fin(app, ["review", "confirm", "acme", str(cid), "--customers"])
    assert done.exit_code == 0, done.output
    assert f"{t['C0']} confirmed as Banking" in done.stdout

    shown = fin(app, ["analytics", "acme"])
    assert shown.exit_code == 0, shown.output
    assert (
        "FY25 core: 4 customers, ₹440,000.00; top 1 63.64%, top 5 100.00%, top 10 100.00%"
        in shown.stdout
    )
    assert "FY25: net 231.25%, gross 78.12%, logo 100.00%" in shown.stdout
    assert "FY25 vs FY24: price ₹70,000.00, volume ₹105,000.00, mix ₹0.00" in shown.stdout
    assert fin(app, ["sql", "acme", "--write", "--sql", "DELETE FROM customers"]).exit_code == 2


def test_the_golden_checks_score_the_analytics(root: Path) -> None:
    sectors(root)
    t = tokens(root)
    k = ("", "", "rupees", "reg.xlsx", "yes")
    good = [
        ("customer_revenue", "FY25", "", "", t["C0"], "2,80,000", *k[2:]),
        ("sector_revenue", "FY25", "", "", "Banking", "3,60,000", *k[2:]),
        ("product_revenue", "FY25", "", "", "Support", "3,00,000", *k[2:]),
        ("top_customers_revenue", "FY25", "", "", "1", "2,80,000", *k[2:]),
        ("retained_revenue", "FY25", "", "", "", "3,70,000", *k[2:]),
        ("pvm_price_effect", "FY25", "", "", "", "70,000", *k[2:]),
        ("pvm_volume_effect", "FY25", "", "", "", "1,05,000", *k[2:]),
        ("pvm_mix_effect", "FY25", "", "", "", "-", *k[2:]),
    ]
    write_golden(root, good)
    assert [c["outcome"] for c in run_eval(run(root), "acme")["checks"]] == ["pass"] * 8

    bad = [(*good[1][:5], "3,60,001", *good[1][6:])]
    write_golden(root, [good[0], *bad])
    result = run_eval(run(root), "acme")
    assert [c["outcome"] for c in result["checks"]] == ["pass", "fail"]
    assert result["checks"][1]["codes"] == ["no_passthrough_list", "unconfirmed_sectors"]
    assert result["counts"]["customers"] == {"confirmed": 3, "proposed": 1}
