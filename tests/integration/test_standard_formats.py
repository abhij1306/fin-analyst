"""Subtotal checks (E402) on the standard formats with no current / non-current split: a Division
III (NBFC) balance sheet and statement of profit and loss, and a bank's Forms A and B (synthetic
PDFs). The lines a Division II statement uses too ("Loans", "Investments", "Other assets") are not
mapped by the seed; they are mapped once for the company, as review would."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from tests.integration.test_real_report_mapping import WIDTH, statement
from tests.synthetic_pdf import write_pdf

runner = CliRunner()

NBFC_BALANCE_SHEET = statement(
    "BALANCE SHEET",
    "ASSETS",
    "Financial assets",
    ("Cash and cash equivalents", "20.00", "15.00"),
    ("Bank balance other than cash and cash equivalents", "5.00", "5.00"),
    "Receivables",
    ("Trade receivables", "3.00", "2.00"),
    ("Loans", "152.00", "123.00"),
    ("Investments", "30.00", "25.00"),
    ("Total financial assets", "210.00", "170.00"),
    "Non-financial assets",
    ("Current tax assets (net)", "1.00", "1.00"),
    ("Deferred tax assets (net)", "2.00", "2.00"),
    ("Property, plant and equipment", "4.00", "3.00"),
    ("Other non-financial assets", "3.00", "4.00"),
    ("Total non-financial assets", "10.00", "10.00"),
    ("Total assets", "220.00", "180.00"),
    "LIABILITIES AND EQUITY",
    "Financial liabilities",
    "Payables",
    ("Trade payables", "4.00", "3.00"),
    ("Debt securities", "40.00", "30.00"),
    ("Borrowings (other than debt securities)", "100.00", "80.00"),
    ("Subordinated liabilities", "10.00", "10.00"),
    ("Total financial liabilities", "154.00", "123.00"),
    "Non-financial liabilities",
    ("Current tax liabilities (net)", "1.00", "1.00"),
    ("Other non-financial liabilities", "5.00", "6.00"),
    ("Total non-financial liabilities", "6.00", "7.00"),
    "Equity",
    ("Equity share capital", "10.00", "10.00"),
    ("Other equity", "50.00", "40.00"),
    ("Total equity", "60.00", "50.00"),
    ("Total liabilities and equity", "220.00", "180.00"),
)
NBFC_PROFIT_AND_LOSS = statement(
    "STATEMENT OF PROFIT AND LOSS",
    "Revenue from operations",
    ("Interest income", "30.00", "25.00"),
    ("Fees and commission income", "5.00", "4.00"),
    ("Net gain on fair value changes", "1.00", "1.00"),
    ("Total revenue from operations", "36.00", "30.00"),
    ("Other income", "1.00", "1.00"),
    ("Total income", "37.00", "31.00"),
    "Expenses",
    ("Finance costs", "15.00", "12.00"),
    ("Fees and commission expense", "1.00", "1.00"),
    ("Impairment on financial instruments", "3.00", "2.00"),
    ("Employee benefits expenses", "5.00", "4.00"),
    ("Depreciation and amortisation expense", "1.00", "1.00"),
    ("Other expenses", "2.00", "2.00"),
    ("Total expenses", "27.00", "22.00"),
    ("Profit before tax", "10.00", "9.00"),
    ("Current tax", "2.00", "2.00"),
    ("Deferred tax", "0.50", "0.25"),
    ("Total tax expense", "2.50", "2.25"),
    ("Profit for the year", "7.50", "6.75"),
)
BANK_BALANCE_SHEET = statement(
    "BALANCE SHEET",
    "CAPITAL AND LIABILITIES",
    ("Capital", "10.00", "10.00"),
    ("Reserves and surplus", "90.00", "80.00"),
    ("Deposits", "800.00", "700.00"),
    ("Borrowings", "60.00", "50.00"),
    ("Other liabilities and provisions", "40.00", "30.00"),
    ("Total", "1000.00", "870.00"),
    "ASSETS",
    ("Cash and balances with Reserve Bank of India", "50.00", "40.00"),
    ("Balances with banks and money at call and short notice", "30.00", "25.00"),
    ("Investments", "250.00", "220.00"),
    ("Advances", "620.00", "545.00"),
    ("Fixed assets", "20.00", "18.00"),
    ("Other assets", "30.00", "22.00"),
    ("Total", "1000.00", "870.00"),
    ("Contingent liabilities", "200.00", "150.00"),
    ("Bills for collection", "15.00", "12.00"),
)
BANK_PROFIT_AND_LOSS = statement(
    "STATEMENT OF PROFIT AND LOSS",
    "I. INCOME",
    ("Interest earned", "80.00", "70.00"),
    ("Other income", "10.00", "8.00"),
    ("Total", "90.00", "78.00"),
    "II. EXPENDITURE",
    ("Interest expended", "50.00", "45.00"),
    ("Operating expenses", "20.00", "18.00"),
    ("Provisions and contingencies", "8.00", "6.00"),
    ("Total", "78.00", "69.00"),
    "III. PROFIT/LOSS",
    ("Net profit for the year", "12.00", "9.00"),
    ("Profit brought forward", "5.00", "2.00"),
    ("Total", "17.00", "11.00"),
    "IV. APPROPRIATIONS",
    ("Transfer to statutory reserve", "3.00", "2.00"),
    ("Balance carried over to balance sheet", "14.00", "9.00"),
    ("Total", "17.00", "11.00"),
)


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def map_for_company(company: str, label: str, account: str) -> None:
    args = ["map", "set", "-c", company, "-s", "balance_sheet", "-l", label, "-a", account]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout


def subtotal_checks(audit_query: Any) -> list[tuple[Any, ...]]:
    return audit_query(
        "SELECT regexp_extract(message, ': (\\w+) vs the sum', 1) AS account, "
        "count(*) FILTER (WHERE passed) FROM validation_results "
        "WHERE check_name = 'subtotal' GROUP BY 1 ORDER BY 1"
    )


def test_a_division_iii_statement_maps_and_every_subtotal_adds_up(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    map_for_company("nbfc", "Loans", "loans")
    map_for_company("nbfc", "Investments", "investments")
    path = write_pdf(
        tmp_path / "nbfc_ar25.pdf", NBFC_BALANCE_SHEET, NBFC_PROFIT_AND_LOSS, width=WIDTH
    )

    result = runner.invoke(app, ["ingest", "nbfc", str(path)])

    assert result.exit_code == 0, result.stdout
    assert "0 unmapped labels" in result.stdout
    assert subtotal_checks(audit_query) == [
        ("pat", 2),
        ("pbt", 2),
        ("revenue", 2),
        ("total_assets", 2),
        ("total_equity", 2),
        ("total_equity_and_liabilities", 2),
        ("total_expenses", 2),
        ("total_income", 2),
        ("total_tax_expense", 2),
    ]
    assert audit_query("SELECT count(*) FROM validation_results WHERE NOT passed") == [(0,)]


def test_a_bank_statement_maps_and_every_subtotal_adds_up(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    map_for_company("bank", "Investments", "investments")
    map_for_company("bank", "Other assets", "other_assets")
    path = write_pdf(
        tmp_path / "bank_ar25.pdf", BANK_BALANCE_SHEET, BANK_PROFIT_AND_LOSS, width=WIDTH
    )

    result = runner.invoke(app, ["ingest", "bank", str(path)])

    assert result.exit_code == 0, result.stdout
    assert "0 unmapped labels" in result.stdout
    assert subtotal_checks(audit_query) == [
        ("pat", 2),
        ("total_assets", 2),
        ("total_equity_and_liabilities", 2),
        ("total_expenses", 2),
        ("total_income", 2),
    ]
    assert audit_query("SELECT count(*) FROM validation_results WHERE NOT passed") == [(0,)]
