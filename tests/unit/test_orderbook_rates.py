"""SPEC-17 item 2: an order in USD counts in rupees only at a rate the user states for the
engagement and the orderbook's date (`fin engagement rate`), recorded with who stated it."""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import DiligenceTableWrite, RateInvalid
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect
from fin.diligence import rates
from fin.diligence.sources import ingest_source
from fin.paths import audit_db_path, company_db_path
from fin.query.write import check_write_sql
from fin.reconcile import run_new, visibility
from tests.engagement_root import engagement_root
from tests.orderbook_fixtures import write_orderbook
from tests.register_fixtures import write_registers

# O-3 (tests/orderbook_fixtures.py): USD 50000, billed 10000, 2024-06-01 to 2025-12-31, as at
# 2025-03-31. At 84.10: value 4205000.00, unbilled 3364000.00, all of it after the as-at date and
# inside the visibility year (2025-04-01 to 2026-03-31).
RATE = "84.10"


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = engagement_root(tmp_path_factory.mktemp("template"))  # acme, engagement FY25
    for path, kind in ((write_registers(root / "reg.xlsx"), "registers"),
                       (write_orderbook(root / "ob.xlsx"), "orderbook")):  # fmt: skip
        ingest_source(run(root), load_config(root), "acme", path, kind, None)
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def orderbook(root: Path) -> dict[str, Any]:
    with connect(company_db_path(root, "acme")) as con:
        data = visibility.visibility(con, 1)
    return next(row for row in data["sources"] if row["source"] == "orderbook")


def o3(root: Path) -> tuple[Any, ...]:
    with connect(company_db_path(root, "acme")) as con:
        row = con.execute(
            "SELECT value_inr, unbilled_inr, rate, rate_note FROM orderbook_current "
            "WHERE order_no = 'O-3'"
        ).fetchone()
    assert row is not None
    return row


def test_a_usd_order_has_no_rupee_amount_until_a_rate_is_stated(root: Path) -> None:
    before = orderbook(root)
    assert o3(root)[:3] == (None, None, None)

    result = rates.state_rate(run(root), "acme", "usd", RATE, "2025-03-31")

    value, unbilled, rate, note = o3(root)
    assert result["orders"] == 1 and result["rate"]["currency"] == "USD"
    assert (value, unbilled, rate) == (Decimal("4205000.00"), Decimal("3364000.00"), Decimal(RATE))
    assert "USD converted at 84.100000 as at 2025-03-31" in note
    after = orderbook(root)
    assert after["remaining_amount"] - before["remaining_amount"] == Decimal("3364000.00")
    assert after["covered_next_year"] - before["covered_next_year"] == Decimal("3364000.00")
    assert after["unusable_items"] == before["unusable_items"] - 1


def test_a_rate_as_at_another_date_does_not_apply(root: Path) -> None:
    result = rates.state_rate(run(root), "acme", "USD", RATE, "2025-03-30")

    assert result["orders"] == 0 and "counts only for an orderbook" in rates.format_stated(result)
    assert o3(root)[0] is None


def test_the_latest_rate_stated_is_used_and_every_one_is_audited(root: Path) -> None:
    first = run(root)
    rates.state_rate(first, "acme", "USD", "80", "2025-03-31")
    first.flush()
    second = run(root)
    rates.state_rate(second, "acme", "USD", RATE, "2025-03-31")
    second.flush()

    assert o3(root)[0] == Decimal("4205000.00")
    with connect(audit_db_path(root)) as con:
        events = con.execute(
            "SELECT run_id, status, detail FROM diligence_events WHERE kind = 'rate' "
            "ORDER BY recorded_at"
        ).fetchall()
    details = [json.loads(detail) for _, _, detail in events]
    assert [e[1] for e in events] == ["stated", "stated"]
    assert [d["rate"] for d in details] == ["80.000000", "84.100000"]
    assert {d["actor"] for d in details} <= {"human", "claude"}
    assert [d["run_id"] for d in details] == [first.run_id, second.run_id]
    assert [r["rate"] for r in rates.list_rates(run(root), "acme")] == [Decimal(RATE)]


def test_the_reconciliation_counts_a_converted_order(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "DELETE FROM register_lines WHERE customer = (SELECT customer FROM register_lines "
            "WHERE invoice_no = 'INV-1002')"
        )
    rates.state_rate(run(root), "acme", "USD", RATE, "2025-03-31")

    (result,) = run_new(run(root), "acme", "orderbook_vs_register", "FY25")

    usd = next(x for x in result["lines"] if x["key"].startswith("O-3"))
    assert usd["left_amount"] == Decimal("4205000.00") and usd["needs_query"]
    assert "USD 50,000.00" in usd["note"] and "converted at 84.100000" in usd["note"]


@pytest.mark.parametrize(
    ("currency", "rate", "as_at"),
    [("INR", "1", "2025-03-31"), ("US", RATE, "2025-03-31"), ("USD", "0", "2025-03-31"),
     ("USD", "84.1234567", "2025-03-31"), ("USD", "abc", "2025-03-31"),
     ("USD", RATE, "31-03-2025"), ("USD", RATE, "2025-W13-1"), ("USD", RATE, "20250331")],
)  # fmt: skip
def test_a_rate_that_cannot_be_stored_is_refused(
    root: Path, currency: str, rate: str, as_at: str
) -> None:
    with pytest.raises(RateInvalid) as caught:
        rates.state_rate(run(root), "acme", currency, rate, as_at)
    assert caught.value.code == "E962"
    assert rates.list_rates(run(root), "acme") == []


def test_fin_sql_write_refuses_the_rates_table(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con, pytest.raises(DiligenceTableWrite):
        check_write_sql(con, "DELETE FROM engagement_rates")
