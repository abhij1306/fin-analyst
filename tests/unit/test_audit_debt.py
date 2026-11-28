"""The figure defects found in the audit of 2026-11-28, each on synthetic data: amounts rounded
half up like every other figure and one rounding for the service-day split."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from fin.db.schema import in_memory
from fin.diligence import bank as diligence_bank
from fin.diligence import payroll
from fin.extract import ledger
from fin.fdd import gst as gst_returns
from fin.map import dictionary
from fin.map.dictionary import Account
from fin.reconcile import cutoff
from fin.reconcile.gst import gstr1_vs_sales_register
from fin.reconcile.payroll_vs_gl import payroll_vs_gl
from fin.reconcile.recast import spread

UNIT = SimpleNamespace(unit=1)


@pytest.fixture
def company() -> Iterator[duckdb.DuckDBPyConnection]:
    with in_memory("company") as con:
        yield con


def test_ledger_payroll_and_bank_amounts_round_half_up() -> None:
    assert ledger.amount("0.125", UNIT, "x") == Decimal("0.13")  # type: ignore[arg-type]
    assert payroll.money("2.345", UNIT, "x") == Decimal("2.35")  # type: ignore[arg-type]
    assert diligence_bank._money("1,000.125") == Decimal("1000.13")


def test_the_service_day_split_is_rounded_once_the_same_way(
    company: duckdb.DuckDBPyConnection,
) -> None:
    # 100.01 over two days: each day's exact share is 50.005
    assert spread(Decimal("100.01"), date(2025, 3, 31), date(2025, 4, 1)) == {
        "2025-03": Decimal("50.01"),
        "2025-04": Decimal("50.00"),
    }
    company.execute(
        "INSERT INTO source_files VALUES (1, 1, 'registers', 'reg.xlsx', 'x', 'a', 'FY25', "
        "'2024-04-01', '2025-03-31', 'audited', 'p', 'loaded', NULL, 'r', now())"
    )
    company.execute(
        'INSERT INTO register_lines (file_id, sheet, "row", kind, customer, invoice_no, '
        "invoice_date, service_start, service_end, amount_inr, run_id) VALUES (1, 'Sales', 2, "
        "'sales', 'CUST-0001', 'S-1', '2025-03-30', '2025-03-31', '2025-04-01', 100.01, 'r')"
    )

    [row] = cutoff._rows(
        company, 1, "sales", date(2025, 3, 31), date(2025, 3, 1), date(2025, 4, 30)
    )

    assert row[10] == Decimal("50.01")  # the recast's March part
    assert company.execute("SELECT day_share(-100.01, 1, 2)").fetchone() == (Decimal("-50.01"),)


def source(con: duckdb.DuckDBPyConnection, file_id: int, kind: str, name: str) -> None:
    con.execute(
        "INSERT INTO source_files VALUES (?, 1, ?, ?, 'x', 'a', 'FY25', '2024-04-01', "
        "'2025-03-31', 'audited', 'p', 'loaded', NULL, 'r', now())",
        [file_id, kind, name],
    )


def test_a_blank_ctc_makes_the_payroll_figure_unknown(company: duckdb.DuckDBPyConnection) -> None:
    source(company, 1, "payroll", "pay.xlsx")
    source(company, 2, "ledger_vouchers", "lv.xlsx")
    company.execute("INSERT INTO payroll_units VALUES (1, 'annual', 'r', now())")
    for row, ctc in ((2, 600000), (3, None)):
        company.execute(
            'INSERT INTO payroll_lines (file_id, sheet, "row", snapshot_date, employee_name, '
            "employee_code, ctc, run_id) VALUES (1, 'Staff', ?, '2025-03-31', 'EMP-0001', 'E1', ?, "
            "'r')",
            [row, ctc],
        )
    company.execute(
        "INSERT INTO ledger_accounts (ledger, status, account, run_id, changed_at) "
        "VALUES ('Salaries', 'confirmed', 'employee_expense', 'r', now())"
    )
    company.execute(
        "INSERT INTO ledger_entries VALUES (2, 'LV', 5, 'Salaries', '2024-06-30', NULL, 'Journal', "
        "'JV/1', 600000, 0, 'r')"
    )
    engagement = {"engagement_id": 1, "periods": "FY25"}

    [line] = payroll_vs_gl(company, "acme", engagement, "FY25").lines

    assert (line["kind"], line["left_amount"]) == (
        "not_reconciled",
        None,
    )  # never 600,000 vs 600,000
    assert line["note"].startswith(
        "not compared: 1 employees have no CTC in the file (pay.xlsx Staff!3)"
    )


def test_a_blank_return_figure_is_unknown_and_not_compared(
    company: duckdb.DuckDBPyConnection,
) -> None:
    assert gst_returns.figure("  ", UNIT, "x") is None  # type: ignore[arg-type]
    source(company, 1, "gstr1", "r1.xlsx")
    source(company, 2, "registers", "reg.xlsx")
    for row, month, value in ((5, "2024-05-01", None), (6, "2024-06-01", 700)):
        company.execute(
            'INSERT INTO gst_returns (file_id, sheet, "row", return_type, period, month, '
            "taxable_value, run_id) VALUES (1, 'GSTR1', ?, 'gstr1', 'FY25', ?, ?, 'r')",
            [row, month, value],
        )
    for row, day in ((2, "2024-05-10"), (3, "2024-06-10")):
        company.execute(
            'INSERT INTO register_lines (file_id, sheet, "row", kind, customer, invoice_no, '
            "invoice_date, amount_inr, run_id) VALUES (2, 'Sales', ?, 'sales', 'CUST-0001', ?, ?, "
            "700, 'r')",
            [row, f"S-{row}", day],
        )

    lines = gstr1_vs_sales_register(company, "acme", {"engagement_id": 1}, "FY25").lines

    assert [(x["key"], x["kind"], x["left_amount"]) for x in lines] == [
        ("2024-05 taxable_value", "not_reconciled", None),
        ("2024-06 taxable_value", "matched", Decimal(700)),
    ]
    assert lines[0]["note"].startswith(
        "not compared: the figure is blank in the return (r1.xlsx GSTR1!5)"
    )


def test_a_component_with_no_confirmed_ledger_is_missing_not_zero(
    company: duckdb.DuckDBPyConnection,
) -> None:
    company.execute(
        "INSERT INTO engagement (engagement_id, periods, query_threshold, status, opened_run_id, "
        "opened_at) VALUES (1, 'FY25', 100000, 'open', 'r', now())"
    )
    source(company, 1, "trial_balance", "tb.xlsx")
    for row, name, account, debit in (
        (2, "Debtors", "trade_receivables", 500),
        (3, "Stock", "inventories", 0),
    ):
        company.execute(
            'INSERT INTO trial_balance (file_id, sheet, "row", period, row_type, ledger, '
            "closing_debit, closing_credit, run_id) "
            "VALUES (1, 'TB', ?, 'FY25', 'ledger', ?, ?, 0, 'r')",
            [row, name, debit],
        )
        company.execute(
            "INSERT INTO ledger_accounts (ledger, status, account, side, run_id, changed_at) "
            "VALUES (?, 'confirmed', ?, 1, 'r', now())",
            [name, account],
        )

    row = company.execute(
        "SELECT trade_receivables, inventories, trade_payables, net_working_capital, "
        "missing_components FROM nwc_year_end"
    ).fetchone()

    # a confirmed nil balance is 0; a component with no ledger is unknown
    assert row == (
        Decimal(500), Decimal(0), None, Decimal(500),
        "other current assets, trade payables, other current liabilities",
    )  # fmt: skip


def test_a_stand_in_total_is_the_signed_sum_of_every_child_with_review_accounts(
    company: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    accounts = {
        "tax_expense": Account("profit_and_loss", "total_tax_expense", 1),
        "tax_refund_credit": Account("profit_and_loss", "total_tax_expense", -1, source="review"),
    }
    monkeypatch.setattr(dictionary, "load_accounts", lambda root: accounts)
    dictionary.copy_review_accounts(Path("unused"), company)
    for account, value in (("pbt", 1000), ("tax_expense", 100), ("tax_refund_credit", 20)):
        company.execute(
            "INSERT INTO stated_figures (account, fiscal_year, period, period_months, period_end, "
            "scope, value, reason, actor, status, run_id, stated_at) "
            "VALUES (?, 'FY25', 'FY25', 12, '2025-03-31', 'standalone', ?, 'test', 'user', "
            "'stated', 'r', now())",
            [account, value],
        )

    [(rate,)] = company.execute(
        "SELECT effective_tax_rate FROM metrics_wide WHERE period = 'FY25'"
    ).fetchall()

    assert rate == Decimal("0.080000")  # (100 - 20) / 1000: the review child, with its sign
