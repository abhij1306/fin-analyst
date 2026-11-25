"""FDD F7 on a synthetic orderbook and register: the load, the backlog sources and visibility,
and orders with no billing / billing with no order as a reconciliation."""

import shutil
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import OrderbookRowInvalid, ReconCannotRun, VisibilityUnavailable
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect, utcnow
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.score import run_eval
from fin.paths import company_db_path
from fin.reconcile import RECONCILIATIONS, run_new, visibility
from tests.engagement_root import engagement_root
from tests.orderbook_fixtures import MAPLE, write_orderbook
from tests.register_fixtures import write_registers

# Register FY25 (write_registers): INV-1001 120000 and INV-1003 160000 (first customer), INV-1002
# 80000 (second customer), each with service 2024-05-01 to 2025-04-30 (365 days, 30 after the
# period end 2025-03-31). The visibility year is 2025-04-01 to 2026-03-31. Threshold 100000.
CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return engagement_root(tmp_path_factory.mktemp("template"))  # acme, engagement FY25, 100000


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def load(root: Path, path: Path, kind: str) -> None:
    ingest_source(run(root), load_config(root), "acme", path, kind, None)


@pytest.fixture
def full(root: Path) -> Path:
    load(root, write_registers(root / "reg.xlsx"), "registers")
    load(root, write_orderbook(root / "ob.xlsx"), "orderbook")
    return root


def rows(root: Path, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql, list(params)).fetchall()


def sources(root: Path) -> dict[str, dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        data = visibility.visibility(con, 1)
    return {row["source"]: row for row in data["sources"]}


# --- the load ---------------------------------------------------------------------------------


def test_an_orderbook_loads_masked_with_its_lineage(full: Path) -> None:
    stored = rows(
        full,
        'SELECT customer, order_no, value, currency, billed, as_at, sheet, "row" '
        "FROM orderbook_lines ORDER BY order_no",
    )
    assert len(stored) == 4
    assert all(c.startswith("CUST-") for c, *_ in stored) and MAPLE not in str(stored)
    assert stored[0][1:4] == ("O-1", Decimal("1200000.00"), "INR")
    assert str(stored[0][5]) == "2025-03-31" and stored[0][6:] == ("Orderbook", 4)
    register_customer = rows(
        full, "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1001'"
    )
    assert stored[0][0] == register_customer[0][0]  # the same customer is the same token


def test_a_row_without_a_value_is_refused(root: Path) -> None:
    load(root, write_registers(root / "reg.xlsx"), "registers")
    with pytest.raises(OrderbookRowInvalid) as caught:
        load(root, write_orderbook(root / "ob.xlsx", bad_value=True), "orderbook")
    assert caught.value.code == "E960" and "no value" in str(caught.value)
    assert rows(root, "SELECT count(*) FROM orderbook_lines")[0][0] == 0


# --- backlog and visibility -------------------------------------------------------------------


def test_the_orderbook_backlog_is_the_unbilled_value_spread_over_its_remaining_term(
    full: Path,
) -> None:
    ob = sources(full)["orderbook"]

    # O-1: 900000 unbilled, 2025-04-01 to 2026-03-31 is all in the year. O-2: 500000 over
    # 2025-04-01 to 2026-09-30 (548 days), 365 of them in the year. O-3 is USD (no rupee amount).
    # O-4: 150000 unbilled, no end date (not spread).
    days = (Decimal(365) + 183) + 0
    o2 = cents(Decimal(500000) * 365 / days)
    assert ob["items"] == 4 and ob["unusable_items"] == 2
    assert ob["remaining_amount"] == Decimal(900000 + 500000 + 150000)
    assert ob["covered_next_year"] == Decimal(900000) + o2
    notes = {n["note"] for n in _notes(full)}
    assert {"order in USD: not converted", "no end date: not spread"} <= notes


def _notes(root: Path) -> list[dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        return visibility.visibility(con, 1)["notes"]


def test_billed_not_earned_is_the_register_amount_for_service_days_after_the_period_end(
    full: Path,
) -> None:
    got = sources(full)["billed_not_earned"]

    share = Decimal(360000) * 30 / 365  # all three invoices, 30 of 365 service days after 31 Mar
    assert got["items"] == 3
    assert abs(got["remaining_amount"] - share) < Decimal("0.05")
    assert got["covered_next_year"] == got["remaining_amount"]  # all inside the next 12 months


def test_core_revenue_and_the_share_covered_are_computed_in_sql(full: Path) -> None:
    ob = sources(full)["orderbook"]

    assert ob["core_revenue"] == Decimal(360000)
    assert ob["covered_pct"] == cents(100 * ob["covered_next_year"] / Decimal(360000))


def test_a_customer_contract_counts_the_value_left_after_the_period_end(full: Path) -> None:
    with connect(company_db_path(full, "acme"), write=True) as con:
        customer = con.execute(
            "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1001'"
        ).fetchone()
        assert customer is not None
        for value in (730000, None):
            con.execute(
                "INSERT INTO contracts (engagement_id, party_kind, counterparty, start_date, "
                "end_date, value, file_name, sha256, page, read_by, run_id, created_at) "
                "VALUES (1, 'customer', ?, '2024-04-01', '2026-03-31', ?, 'c.pdf', 'x', 2, "
                "'claude', 'r', ?)",
                [customer[0], value, utcnow()],
            )

    got = sources(full)["contract"]

    # 730 days (2024-04-01 to 2026-03-31), 365 of them after the period end and all in the year
    assert got["items"] == 2 and got["unusable_items"] == 1
    assert got["remaining_amount"] == Decimal(365000)
    assert got["covered_next_year"] == Decimal(365000)


def test_visibility_with_nothing_loaded_is_refused(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con, pytest.raises(VisibilityUnavailable) as e:
        visibility.visibility(con, 1)
    assert e.value.code == "E961"


def test_a_source_not_loaded_is_said_so(root: Path) -> None:
    load(root, write_orderbook(root / "ob.xlsx"), "orderbook")

    text = visibility.format_visibility(visibility_data(root))

    assert "customer contracts remaining: not loaded" in text
    assert "core revenue not loaded" in text and "not added together" in text


def visibility_data(root: Path) -> dict[str, Any]:
    with connect(company_db_path(root, "acme")) as con:
        return visibility.visibility(con, 1)


# --- orderbook_vs_register --------------------------------------------------------------------


def test_orders_with_no_billing_and_billing_with_no_order(root: Path) -> None:
    load(root, write_registers(root / "reg.xlsx"), "registers")
    load(root, write_orderbook(root / "ob.xlsx", omit_second=True), "orderbook")

    (result,) = run_new(run(root), "acme", "orderbook_vs_register", "FY25")

    by_key = {line["key"].split(" (")[0]: line for line in result["lines"]}
    order = by_key["O-2"]  # Maple has an order and no register billing
    assert (order["kind"], order["left_amount"], order["needs_query"]) == (
        "left_only", Decimal("500000.00"), True
    )  # fmt: skip
    assert "billed to date per the orderbook" in order["note"]
    billing = [x for x in result["lines"] if x["key"].startswith("billing ")]
    assert len(billing) == 1 and billing[0]["kind"] == "right_only"
    assert billing[0]["right_amount"] == Decimal("80000.00") and not billing[0]["needs_query"]
    assert "O-1" not in by_key  # the first customer is billed in the register


def test_a_foreign_currency_order_has_no_rupee_amount_and_is_never_queried(full: Path) -> None:
    with connect(company_db_path(full, "acme"), write=True) as con:
        con.execute(
            "DELETE FROM register_lines WHERE customer = (SELECT customer FROM register_lines "
            "WHERE invoice_no = 'INV-1002')"
        )

    (result,) = run_new(run(full), "acme", "orderbook_vs_register", "FY25")

    usd = next(x for x in result["lines"] if x["key"].startswith("O-3"))
    assert usd["left_amount"] is None and not usd["needs_query"] and "USD" in usd["note"]


def test_the_reconciliation_needs_both_inputs(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con, pytest.raises(ReconCannotRun):
        RECONCILIATIONS["orderbook_vs_register"][0](
            con, "acme", {"engagement_id": 1, "periods": "FY25"}, "FY25"
        )


# --- golden evaluation ------------------------------------------------------------------------


def test_golden_lines_for_visibility_score_pass_and_a_planted_wrong_one_fails(full: Path) -> None:
    ob = sources(full)["orderbook"]
    golden = golden_path(full, "acme")
    golden.parent.mkdir(parents=True, exist_ok=True)

    def line(kind: str, expected: Decimal) -> str:
        values = {c: "" for c in COLUMNS} | {
            "kind": kind, "period": "FY25", "ledger": "orderbook", "expected": str(expected),
            "unit": "rupees", "source": "ob.xlsx Orderbook", "checked": "yes",
        }  # fmt: skip
        return ",".join(values[c] for c in COLUMNS)

    golden.write_text(
        "\n".join(
            [
                ",".join(COLUMNS),
                line("backlog_remaining", ob["remaining_amount"]),
                line("visibility_covered", ob["covered_next_year"] + 1000),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_eval(run(full), "acme")

    assert [c["outcome"] for c in result["checks"]] == ["pass", "fail"]
