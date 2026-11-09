"""The company metric views (src/fin/db/schema/company_views.sql) on facts written straight into a
fresh company database: one FY25 standalone document, values in rupees."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.db.conn import connect
from fin.db.schema import ensure_database

Line = tuple[str, str, str, str]  # account, raw label, section, value


@pytest.fixture
def company(tmp_path: Path) -> Path:
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (file_name, sha256, version, fiscal_year, scope, status, "
            "archive_path, run_id, ingested_at, period_end) VALUES ('ar25.pdf', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'a', 'r', now(), DATE '2025-03-31')"
        )
    return path


def load(path: Path, *lines: Line) -> None:
    with connect(path, write=True) as con:
        for account, label, section, value in lines:
            [(raw_id,)] = con.execute(
                "INSERT INTO raw_values (doc_id, page, statement, section, raw_label, "
                "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (1, 1, "
                "'balance_sheet', ?, ?, ?, '(Rs.)', 1, 'r') RETURNING raw_id",
                [section, label, value],
            ).fetchall()
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r', 'FY25', 12)",
                [raw_id, account, value],
            )


def query(path: Path, sql: str) -> list[Any]:
    with connect(path) as con:
        return con.execute(sql).fetchall()


def metric(path: Path, name: str) -> Any:
    rows = query(path, f"SELECT value FROM metrics WHERE metric = '{name}'")
    return rows[0][0] if rows else "absent"


def trade_payables(path: Path) -> list[Any]:
    return query(
        path,
        "SELECT CAST(value AS VARCHAR), len(fact_ids) FROM current_facts "
        "WHERE account = 'trade_payables'",
    )


def test_two_equal_lines_without_a_total_are_summed(company: Path) -> None:
    load(
        company,
        ("trade_payables", "Dues of micro and small enterprises", "Trade payables", "500.00"),
        ("trade_payables", "Dues of others", "Trade payables", "500.00"),
    )
    assert trade_payables(company) == [("1000.00", 2)]


def test_of_two_equal_lines_one_labelled_total_is_the_total(company: Path) -> None:
    load(
        company,
        ("trade_payables", "Trade payables", "Current liabilities", "500.00"),
        ("trade_payables", "Total trade payables", "Current liabilities", "500.00"),
    )
    assert trade_payables(company) == [("500.00", 1)]


@pytest.mark.parametrize(
    ("label", "heading"),
    [
        ("Trade payables", "Trade payables"),
        ("(b) Trade payables", "Trade payables"),
        ("Trade payables", "(b) Trade payables"),
        ("Trade payables", "ii) Trade payables"),
        ("Trade payables", "b. Trade payables"),
        ("Trade payables", "(ii) Trade payables:"),
    ],
)
def test_of_two_equal_lines_the_one_the_other_sits_under_is_the_total(
    company: Path, label: str, heading: str
) -> None:
    load(
        company,
        ("trade_payables", label, "Current liabilities", "500.00"),
        ("trade_payables", "Dues of others", heading, "500.00"),
    )
    assert trade_payables(company) == [("500.00", 1)]


def test_a_line_equal_to_the_sum_of_two_others_is_their_total(company: Path) -> None:
    load(
        company,
        ("trade_payables", "Trade payables", "", "500.00"),
        ("trade_payables", "Dues of micro and small enterprises", "", "200.00"),
        ("trade_payables", "Dues of others", "", "300.00"),
    )
    assert trade_payables(company) == [("500.00", 1)]


CURRENT_LIABILITIES = ("total_current_liabilities", "Total current liabilities", "", "1000.00")


def test_current_assets_not_printed_sum_every_current_line(company: Path) -> None:
    load(
        company,
        CURRENT_LIABILITIES,
        ("inventories", "Inventories", "", "100.00"),
        ("cash", "Cash and cash equivalents", "", "50.00"),
        ("other_bank_balances", "Other bank balances", "", "25.00"),
        ("loans_current", "Loans", "", "200.00"),
        ("other_financial_assets_current", "Other financial assets", "", "75.00"),
        ("tax_assets_current", "Current tax assets (net)", "", "30.00"),
        ("assets_held_for_sale", "Assets held for sale", "", "20.00"),
    )
    assert metric(company, "current_ratio") == Decimal("0.500000")


def test_cash_and_bank_balances_is_not_added_to_its_own_parts(company: Path) -> None:
    load(
        company,
        CURRENT_LIABILITIES,
        ("inventories", "Inventories", "", "100.00"),
        ("cash_and_bank_balances", "Cash and bank balances", "", "300.00"),
        ("cash", "Cash and cash equivalents", "Cash and bank balances", "200.00"),
        ("other_bank_balances", "Other bank balances", "Cash and bank balances", "100.00"),
    )
    assert metric(company, "current_ratio") == Decimal("0.400000")
    assert query(
        company,
        "SELECT account, CAST(value AS VARCHAR) FROM metric_inputs WHERE metric = "
        "'current_ratio' AND stands_in_for = 'total_current_assets' ORDER BY account",
    ) == [("cash_and_bank_balances", "300.00"), ("inventories", "100.00")]


def test_current_liabilities_not_printed_include_liabilities_held_for_sale(
    company: Path,
) -> None:
    load(
        company,
        ("total_current_assets", "Total current assets", "", "900.00"),
        ("trade_payables", "Trade payables", "", "200.00"),
        ("liabilities_held_for_sale", "Liabilities held for sale", "", "100.00"),
    )
    assert metric(company, "current_ratio") == Decimal("3.000000")


def test_gross_margin_takes_purchases_and_the_change_in_inventories_as_printed(
    company: Path,
) -> None:
    load(
        company,
        ("revenue", "Revenue from operations", "", "1000.00"),
        ("cost_of_materials", "Cost of materials consumed", "", "400.00"),
        ("purchases_stock_in_trade", "Purchases of stock-in-trade", "", "150.00"),
        ("change_in_inventories", "Changes in inventories", "", "-50.00"),  # inventories rose
    )
    assert metric(company, "gross_margin") == Decimal("0.500000")


def test_gross_margin_without_purchases_or_inventory_change_is_on_materials(
    company: Path,
) -> None:
    load(
        company,
        ("revenue", "Revenue from operations", "", "1000.00"),
        ("cost_of_materials", "Cost of materials consumed", "", "400.00"),
    )
    assert metric(company, "gross_margin") == Decimal("0.600000")


def test_gross_margin_has_no_value_without_cost_of_materials(company: Path) -> None:
    load(
        company,
        ("revenue", "Revenue from operations", "", "1000.00"),
        ("purchases_stock_in_trade", "Purchases of stock-in-trade", "", "150.00"),
    )
    assert metric(company, "gross_margin") is None


NBFC = (
    ("interest_income", "Interest income", "", "270.00"),
    ("finance_cost", "Finance costs", "", "150.00"),
    ("total_income", "Total income", "", "370.00"),
    ("total_expenses", "Total expenses", "", "270.00"),
)


def test_cost_to_income_takes_out_the_credit_charge(company: Path) -> None:
    load(company, *NBFC, ("impairment_on_financial_instruments", "Impairment", "", "30.00"))
    assert metric(company, "cost_to_income") == Decimal("0.409091")  # (270 - 150 - 30) / 220


def test_cost_to_income_has_no_value_without_a_credit_charge(company: Path) -> None:
    load(company, *NBFC)
    assert metric(company, "cost_to_income") == "absent"
    assert metric(company, "net_interest_income") == Decimal("120.000000")
