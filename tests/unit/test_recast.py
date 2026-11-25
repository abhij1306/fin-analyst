"""The service-period recast (fin.reconcile.recast, recast_run, contracts) and gate G3 on a
synthetic sales register: a line billed up front for 12 months recasts into the right periods and
proposes the adjustment; lines without service dates are listed (and queried at or above the
threshold)."""

import csv
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import (
    ContractNotOnRegister,
    ContractReadingInvalid,
    RecastCannotRun,
)
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import gates
from fin.diligence.adjustments import list_adjustments, propose
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.reconcile import contracts, items
from fin.reconcile.recast import months_of, spread
from fin.reconcile.recast_run import run_recast
from tests.register_fixtures import sales_line, write_registers
from tests.tally_fixtures import CUSTOMERS

NO_DATES = {"Service Start Date": None, "Service End Date": None}


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25,FY26', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


def load(root: Path, tmp_path: Path, extra: list[list[object]]) -> None:
    path = write_registers(tmp_path / "reg.xlsx", sales_extra=extra)
    ingest_source(run(root), load_config(root), "acme", path, "registers", None)


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    # INV-1001..1003: 12 months of service from 1 May 2024, billed in May 2024 (FY25). INV-1004:
    # Rs 150000 with no service dates (at the threshold or above); INV-1005: Rs 50000, none either.
    load(
        data,
        tmp_path,
        [
            sales_line(4, CUSTOMERS[0], 150000, **NO_DATES),
            sales_line(5, CUSTOMERS[1], 50000, **NO_DATES),
        ],
    )
    return data


def rows(root: Path, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql, list(params)).fetchall()


def period_line(result: dict[str, Any]) -> dict[str, Any]:
    [line] = [x for x in result["lines"] if x["level"] == "period"]
    return line


def test_spread_is_straight_line_by_service_days_and_adds_up() -> None:
    shares = spread(Decimal("120000.00"), date(2024, 5, 1), date(2025, 4, 30))

    assert list(shares) == [f"2024-{m:02d}" for m in range(5, 13)] + [
        f"2025-0{m}" for m in range(1, 5)
    ]
    assert sum(shares.values()) == Decimal("120000.00")
    assert shares["2024-05"] == (Decimal(120000) * 31 / 365).quantize(Decimal("0.01"))


def test_months_of_an_annual_period() -> None:
    assert months_of("FY25", 4)[0] == "2024-04"
    assert months_of("FY25", 4)[-1] == "2025-03"
    assert months_of("FY25", 1)[0] == "2025-01"
    assert months_of("FY25", 1)[-1] == "2025-12"


def test_a_line_billed_up_front_recasts_into_the_right_periods(root: Path) -> None:
    fy25, fy26 = run_recast(run(root), "acme", None)

    # 1 May 2024 to 31 March 2025 is 335 of the 365 service days: FY25 keeps that share of
    # the three dated invoices (Rs 360000 in all) and the rest moves to FY26.
    recognised = (Decimal(360000) * 335 / 365).quantize(Decimal("0.01"))
    assert abs(Decimal(period_line(fy25)["left_amount"]) - (recognised + 200000)) <= Decimal("0.05")
    assert period_line(fy25)["right_amount"] == Decimal("560000.00")  # booked by invoice date
    assert Decimal(period_line(fy25)["difference"]) + Decimal(period_line(fy26)["difference"]) == 0
    assert period_line(fy26)["right_amount"] == 0
    months = {x["key"]: x["left_amount"] for x in fy26["lines"] if x["level"] == "month"}
    assert list(months) == ["2025-04"]
    adjustments = list_adjustments(run(root), "acme")
    assert [(a["period"], a["category"], a["status"], a["account"]) for a in adjustments] == [
        ("FY25", "recast", "proposed", "revenue"),
        ("FY26", "recast", "proposed", "revenue"),
    ]
    for adjustment, result in zip(adjustments, (fy25, fy26), strict=True):
        difference = period_line(result)["difference"]
        assert (adjustment["revenue_effect"], adjustment["cost_effect"]) == (difference, 0)
        assert adjustment["amount"] == difference
        assert (
            adjustment["source_ref"]
            == f"service_recast {result['period']} (recon #{result['recon_id']})"
        )


def test_a_line_without_service_dates_is_listed_and_queried_at_the_threshold(root: Path) -> None:
    [fy25, _] = run_recast(run(root), "acme", None)

    undated = {x["key"]: x for x in fy25["lines"] if x["kind"] == "undated"}
    assert set(undated) == {"INV-1004", "INV-1005"}
    assert undated["INV-1004"]["left_amount"] == Decimal("150000.00")
    assert undated["INV-1004"]["needs_query"]
    assert not undated["INV-1005"]["needs_query"]
    assert "recognised on the invoice date" in undated["INV-1004"]["note"]
    assert "reg.xlsx" in undated["INV-1004"]["left_source"]
    # the undated invoices are booked and recognised on the same date: no effect of their own
    schedule = {x["key"] for x in fy25["lines"] if x["level"] == "schedule"}
    assert not schedule & {"INV-1004", "INV-1005"}

    query, _ = items.raise_from_items(
        run(root), "acme", [undated["INV-1004"]["item_id"]], "Service dates for INV-1004?"
    )
    assert query["amount"] == Decimal("150000.00")


def test_running_again_keeps_one_proposal_and_replaces_a_stale_one(root: Path) -> None:
    run_recast(run(root), "acme", None)
    run_recast(run(root), "acme", None)
    assert len(list_adjustments(run(root), "acme")) == 2

    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE register_lines SET amount_inr = 130000 WHERE invoice_no = 'INV-1001'")
    run_recast(run(root), "acme", "FY25")

    by_status = [
        (a["period"], a["status"], a["reason"]) for a in list_adjustments(run(root), "acme")
    ]
    assert by_status[0] == ("FY25", "rejected", "superseded by recast run #5")
    assert ("FY25", "proposed", None) in by_status
    assert len(by_status) == 3


def test_the_period_must_be_annual_and_a_register_loaded(root: Path, tmp_path: Path) -> None:
    run_root = run(root)
    with pytest.raises(RecastCannotRun, match="annual period"):
        run_recast(run_root, "acme", "FY24")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE source_files SET status = 'superseded'")
    run_root = run(root)
    with pytest.raises(RecastCannotRun, match="no loaded sales register"):
        run_recast(run_root, "acme", None)


def g3(root: Path) -> list[tuple[bool, str]]:
    with connect(company_db_path(root, "acme")) as con:
        ctx = gates.context(con, root, "acme")
        [gate] = [g for g in gates.evaluate(ctx) if g["gate"] == "G3"]
    return [(x["ok"], x["text"]) for x in gate["checklist"]]


def test_gate_g3_needs_the_recast_the_queries_and_the_adjustments(root: Path) -> None:
    assert [ok for ok, _ in g3(root)] == [False, False]
    assert "has not run" in g3(root)[0][1]

    [fy25, fy26] = run_recast(run(root), "acme", None)
    assert [ok for ok, _ in g3(root)] == [False, True]
    assert "no service dates and are not in a query" in g3(root)[0][1]

    item = next(x["item_id"] for x in fy25["lines"] if x["key"] == "INV-1004")
    items.raise_from_items(run(root), "acme", [item], "Service dates for INV-1004?")
    assert [ok for ok, _ in g3(root)] == [True, True]

    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE adjustments SET status = 'rejected', reason = 'no' WHERE period = 'FY26'"
        )
        con.execute("DELETE FROM adjustments WHERE period = 'FY25'")
    assert [ok for ok, _ in g3(root)] == [False, True]
    assert "has no proposed or decided recast adjustment" in g3(root)[0][1]

    run_recast(run(root), "acme", "FY25")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE register_lines SET amount_inr = 1 WHERE invoice_no = 'INV-1002'")
    assert "data that has changed since" in g3(root)[0][1]


def test_g3_has_nothing_to_recast_without_a_sales_register(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE source_files SET status = 'superseded'")

    assert g3(root) == [(True, "no sales register loaded: nothing to recast")]


def write_reading(path: Path, token: str, **over: str) -> Path:
    row = {
        "customer": token, "invoice_no": "INV-1004", "start": "2024-05-01", "end": "2025-04-30",
        "value": "1,50,000.00", "billing_terms": "annual, in advance", "page": "3",
    }  # fmt: skip
    row.update(over)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_a_contract_is_evidence_beside_the_line_and_not_the_schedule(
    root: Path, tmp_path: Path
) -> None:
    [(token,)] = rows(root, "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1004'")
    document = tmp_path / "contract.pdf"
    document.write_bytes(b"%PDF synthetic")
    before = run_recast(run(root), "acme", "FY25")[0]

    [stored] = contracts.load(
        run(root), "acme", document, write_reading(tmp_path / "r.csv", CUSTOMERS[0])
    )
    after = run_recast(run(root), "acme", "FY25")[0]

    assert (stored["customer"], stored["page"], stored["read_by"]) == (token, 3, "claude")
    assert stored["value"] == Decimal("150000.00")
    assert stored["file_name"] == "contract.pdf"
    note = next(x["note"] for x in after["lines"] if x["key"] == "INV-1004")
    assert (
        f"contract #{stored['contract_id']} (contract.pdf p.3) says 2024-05-01 to 2025-04-30"
        in note
    )
    assert period_line(after)["difference"] == period_line(before)["difference"]  # no schedule
    assert (
        len(contracts.load(run(root), "acme", document, write_reading(tmp_path / "r.csv", token)))
        == 1
    )


def test_a_bad_contract_reading_is_refused_naming_the_line(root: Path, tmp_path: Path) -> None:
    document = tmp_path / "contract.pdf"
    document.write_bytes(b"x")
    bad = write_reading(tmp_path / "bad.csv", CUSTOMERS[0], end="2024-01-01")
    run_root = run(root)
    with pytest.raises(ContractReadingInvalid, match="line 2: end is before start"):
        contracts.load(run_root, "acme", document, bad)
    unknown = write_reading(tmp_path / "unknown.csv", "Nobody Ltd")
    run_root = run(root)
    with pytest.raises(ContractNotOnRegister, match="line 2") as error:
        contracts.load(run_root, "acme", document, unknown)
    assert "Nobody" not in str(error.value)
    assert rows(root, "SELECT count(*) FROM contracts") == [(0,)]


def test_recast_figures_are_scored_by_the_eval(root: Path) -> None:
    [fy25, _] = run_recast(run(root), "acme", None)
    head = period_line(fy25)
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True)
    lines = [
        (
            "recast_revenue",
            "FY25",
            "",
            "",
            "",
            f"{head['left_amount']}",
            "rupees",
            "reg.xlsx",
            "yes",
        ),
        (
            "recast_difference",
            "FY25",
            "",
            "",
            "",
            f"{head['difference']}",
            "rupees",
            "reg.xlsx",
            "yes",
        ),
        ("recast_undated", "FY25", "", "", "", "2,00,000", "rupees", "reg.xlsx", "yes"),
        ("recast_revenue", "FY26", "", "", "", "1", "rupees", "reg.xlsx", "yes"),
    ]

    def score() -> list[str]:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(lines)
        return [c["outcome"] for c in run_eval(run(root), "acme")["checks"]]

    assert score() == ["pass", "pass", "pass", "fail"]
    lines[2] = (*lines[2][:5], "2,00,001", *lines[2][6:])
    assert score() == ["pass", "pass", "fail", "fail"]
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("DELETE FROM recon_runs")
    result = run_eval(run(root), "acme")
    assert [c["outcome"] for c in result["checks"]] == ["missing"] * 4
    assert result["checks"][0]["codes"] == ["recast_not_run"]


def set_status(root: Path, where: str, status: str, reason: str | None = None) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            f"UPDATE adjustments SET status = ?, reason = ? WHERE {where}", [status, reason]
        )


def test_a_hand_proposed_recast_adjustment_is_never_replaced_by_a_run(root: Path) -> None:
    run_recast(run(root), "acme", None)
    manual = propose(
        run(root), "acme", period="FY25", scope="standalone", category="recast", amount=Decimal(7),
        description="by hand", source_ref="note.xlsx Sheet1!A1",
    )  # fmt: skip
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE register_lines SET amount_inr = 130000 WHERE invoice_no = 'INV-1001'")

    run_recast(run(root), "acme", "FY25")

    states = {a["adj_id"]: a["status"] for a in list_adjustments(run(root), "acme")}
    assert states[manual["adj_id"]] == "proposed"
    assert list(states.values()).count("rejected") == 1  # fin's own earlier proposal only


def missing_adjustment(root: Path) -> bool:
    return any("has no proposed or decided" in text for _, text in g3(root))


def test_a_rejected_residual_stands_and_other_scopes_do_not_count(root: Path) -> None:
    run_recast(run(root), "acme", None)
    set_status(root, "period = 'FY25'", "accepted")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE register_lines SET amount_inr = 130000 WHERE invoice_no = 'INV-1001'")
    run_recast(run(root), "acme", "FY25")
    [residual] = [
        a for a in list_adjustments(run(root), "acme", "proposed") if a["period"] == "FY25"
    ]
    assert not missing_adjustment(root)

    set_status(root, f"adj_id = {residual['adj_id']}", "rejected", "not now")

    assert not missing_adjustment(root)  # the user's rejection of the residual decides it
    run_recast(run(root), "acme", "FY25")
    assert len([a for a in list_adjustments(run(root), "acme") if a["period"] == "FY25"]) == 2

    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("DELETE FROM adjustments WHERE period = 'FY26'")
        con.execute(
            "INSERT INTO adjustments (engagement_id, period, scope, category, amount, "
            "revenue_effect, cost_effect, description, source_ref, status, created_run_id, "
            "created_at, status_run_id, status_at) VALUES (1, 'FY26', 'consolidated', 'recast', "
            "1, 1, 0, 'd', 'x', 'accepted', 'r', now(), 'r', now())"
        )
    run_recast(run(root), "acme", "FY26")
    assert [
        a["scope"] for a in list_adjustments(run(root), "acme", "proposed") if a["period"] == "FY26"
    ] == ["standalone"]


def test_billing_terms_are_masked_and_a_token_of_any_kind_is_found(
    root: Path, tmp_path: Path
) -> None:
    document = tmp_path / "contract.pdf"
    document.write_bytes(b"x")
    reading = write_reading(
        tmp_path / "r.csv", CUSTOMERS[0], billing_terms=f"invoiced to {CUSTOMERS[0]} yearly"
    )

    [stored] = contracts.load(run(root), "acme", document, reading)

    assert CUSTOMERS[0] not in stored["billing_terms"]
    assert stored["billing_terms"].startswith("invoiced to CUST-")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE mask_values SET kind = 'vendor' WHERE real_value = ?", [CUSTOMERS[0]])
    assert len(contracts.load(run(root), "acme", document, reading)) == 1
