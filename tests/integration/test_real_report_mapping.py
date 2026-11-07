"""Mapping and validation of real annual-report layouts (synthetic PDFs): balance-sheet parts under
sub-headings, OCI, exceptional items printed as a gain, per-share figures, and the fix path for a
document held with a finding (`fin map set`, then ingest it again with --force)."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path
from tests.synthetic_pdf import Page, write_pdf

runner = CliRunner()
WIDTH = 760  # wide enough for the longest Schedule III labels left of the figures
FY25_X, FY24_X = 570, 660
Line = tuple[str, str, str] | str  # (label, FY25, FY24), or a heading


def statement(title: str, *lines: Line) -> Page:
    page: Page = [
        (50, 50, title),
        (50, 65, "(Rs. in Lakhs)"),
        (FY25_X - 10, 85, "31 March 2025"),
        (FY24_X - 10, 85, "31 March 2024"),
    ]
    for n, line in enumerate(lines):
        y = 105 + 15 * n
        if isinstance(line, str):
            page.append((50, y, line))
        else:
            label, fy25, fy24 = line
            page += [(50, y, label), (FY25_X, y, fy25), (FY24_X, y, fy24)]
    return page


BALANCE_SHEET = statement(
    "BALANCE SHEET",
    "Non-current assets",
    ("Property, plant and equipment", "60.00", "50.00"),
    "Financial Assets",
    ("Loans", "5.00", "5.00"),
    ("Security deposits", "-", "-"),  # unmapped but nil: does not stop the subtotal check
    ("Total non-current assets", "65.00", "55.00"),
    "Current assets",
    ("Inventories", "10.00", "9.00"),
    "Financial assets",
    ("Cash and cash equivalents", "20.00", "16.00"),
    ("Loans", "5.00", "10.00"),
    ("Total current assets", "35.00", "35.00"),
    ("TOTAL ASSETS", "100.00", "90.00"),
    "EQUITY AND LIABILITIES",
    ("Equity share capital", "10.00", "10.00"),
    ("Other equity", "50.00", "40.00"),
    ("Total equity", "60.00", "50.00"),
    "Financial liabilities",  # no "Non-current liabilities" heading: the total equity line says so
    ("Borrowings", "15.00", "20.00"),
    ("Total non-current liabilities", "15.00", "20.00"),
    "Financial liabilities",
    ("Borrowings", "10.00", "8.00"),
    "Trade payables",
    ("Total outstanding dues of micro enterprises and small enterprises", "5.00", "4.00"),
    (
        "Total outstanding dues of creditors other than micro enterprises and small enterprises",
        "10.00",
        "8.00",
    ),
    ("Total current liabilities", "25.00", "20.00"),
    ("Total liabilities", "40.00", "40.00"),
    ("TOTAL EQUITY AND LIABILITIES", "100.00", "90.00"),
)
PROFIT_AND_LOSS = statement(
    "STATEMENT OF PROFIT AND LOSS",
    ("Revenue from operations", "100.00", "90.00"),
    ("Other income", "2.00", "1.00"),
    ("Total income", "102.00", "91.00"),
    ("Cost of materials consumed", "60.00", "50.00"),
    ("Employee benefits expense", "20.00", "18.00"),
    ("Total expenses", "80.00", "68.00"),
    ("Profit before exceptional items and tax", "22.00", "23.00"),
    ("Exceptional items", "3.00", "-"),  # a gain, added: 22 + 3 = 25
    ("Profit before tax", "25.00", "23.00"),
    ("Current tax", "5.00", "5.00"),
    ("Deferred tax", "1.00", "-"),
    ("Total tax expense", "6.00", "5.00"),
    ("Profit for the year", "19.00", "18.00"),
    "Items that will not be reclassified to profit or loss",
    ("Remeasurement of defined benefit plans", "(1.00)", "0.50"),
    ("Income tax relating to items that will not be reclassified to profit or loss", "0.25", "-"),
    ("Other comprehensive income for the year, net of tax", "(0.75)", "0.50"),
    ("Total comprehensive income for the year", "18.25", "18.50"),
    "Earnings per equity share",
    ("Nominal value per share", "10.00", "10.00"),  # unmapped, per share: not part of a subtotal
    ("Basic (in Rs.)", "12.34", "11.00"),
    ("Diluted (in Rs.)", "12.30", "11.00"),
)


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def query(root: Path, company: str, sql: str) -> list[Any]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def test_a_real_layout_maps_fully_and_every_subtotal_check_runs(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    path = write_pdf(tmp_path / "acme_ar25.pdf", BALANCE_SHEET, PROFIT_AND_LOSS, width=WIDTH)

    result = runner.invoke(app, ["ingest", "acme", str(path)])

    assert result.exit_code == 0, result.stdout
    assert "2 unmapped labels" in result.stdout  # security deposits, nominal value per share
    assert query(
        root,
        "acme",
        "SELECT account, value FROM facts WHERE fiscal_year = 'FY25' AND account IN ("
        "'lt_borrowings', 'st_borrowings', 'loans_non_current', 'loans_current', "
        "'total_liabilities', 'eps_basic', 'eps_diluted', 'oci', 'exceptional_items') ORDER BY 1",
    ) == [
        ("eps_basic", Decimal("12.34")),  # rupees per share as printed, not x 1 lakh
        ("eps_diluted", Decimal("12.30")),
        ("exceptional_items", Decimal("300000.00")),  # as printed
        ("loans_current", Decimal("500000.00")),
        ("loans_non_current", Decimal("500000.00")),
        ("lt_borrowings", Decimal("1500000.00")),
        ("oci", Decimal("-75000.00")),
        ("st_borrowings", Decimal("1000000.00")),
        ("total_liabilities", Decimal("4000000.00")),
    ]
    checks = audit_query(
        "SELECT fiscal_year, count(*), count(*) FILTER (WHERE passed) FROM validation_results "
        "WHERE check_name = 'subtotal' GROUP BY 1 ORDER BY 1"
    )
    assert checks == [("FY24", 16, 16), ("FY25", 16, 16)]
    assert (
        audit_query("SELECT message FROM events WHERE message LIKE 'subtotal check not run%'") == []
    )
    assert audit_query(
        "SELECT fiscal_year, message LIKE '%printed as a gain and added%' FROM validation_results "
        "WHERE check_name = 'subtotal' AND message LIKE '%pbt vs%' ORDER BY 1"
    ) == [("FY24", False), ("FY25", True)]


def test_a_subtotal_that_does_not_add_up_with_either_sign_is_a_finding(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    broken: Page = [
        (x, y, "28.00") if text == "25.00" and x == FY25_X else (x, y, text)
        for x, y, text, *_ in PROFIT_AND_LOSS
    ]
    path = write_pdf(tmp_path / "acme_ar25.pdf", broken, width=WIDTH)

    result = runner.invoke(app, ["ingest", "acme", str(path)])

    assert result.exit_code == 0, result.output
    assert "held as doc_id 1" in result.stdout
    assert query(root, "acme", "SELECT DISTINCT code, status FROM findings") == [("E402", "open")]
    assert audit_query(
        "SELECT CAST(expected AS VARCHAR), CAST(actual AS VARCHAR), passed FROM validation_results "
        "WHERE fiscal_year = 'FY25' AND message LIKE '%pbt vs the sum of its lines%'"
    ) == [("1900000.00", "2800000.00", False)]


OLD_FORMAT = (
    statement(
        "BALANCE SHEET",
        ("Share capital", "10.00", "10.00"),
        ("Reserves and surplus", "30.00", "20.00"),
        ("Sundry creditors", "10.00", "10.00"),
        ("TOTAL SOURCES", "50.00", "40.00"),
        ("Fixed assets (net block)", "20.00", "20.00"),
        ("Cash & bank balances", "30.00", "20.00"),
        ("TOTAL APPLICATION", "50.00", "40.00"),
    ),
    statement(
        "CASH FLOW STATEMENT",
        ("Closing cash & bank balances", "8.00", "6.00"),  # deposits are not cash equivalents
    ),
)


def test_a_held_document_loads_after_a_company_mapping_fix(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    path = write_pdf(tmp_path / "acme_ar24.pdf", *OLD_FORMAT, width=WIDTH)

    held = runner.invoke(app, ["ingest", "acme", str(path)])

    assert held.exit_code == 0, held.output
    assert "acme/acme_ar24.pdf: held as doc_id 1 version 1" in held.stdout
    rows = json.loads(runner.invoke(app, ["findings", "list", "acme", "--json"]).stdout)
    assert [
        (f["code"], f["period"], f["fin_printed"], f["fin_source"])
        + (f["other_printed"], f["other_source"])
        for f in rows
    ] == [
        ("E403", "FY24", "6.00", "page 2", "20.00", "page 1"),
        ("E403", "FY25", "8.00", "page 2", "30.00", "page 1"),
    ]

    mapped = runner.invoke(
        app,
        [
            "map",
            "set",
            "acme",
            "--statement",
            "balance_sheet",
            "--label",
            "Cash & bank balances",
            "--account",
            "cash_and_bank_balances",
        ],
    )
    loaded = runner.invoke(app, ["ingest", "acme", str(path), "--force"])

    assert mapped.exit_code == 0, mapped.stdout
    assert "acme: balance_sheet 'Cash & bank balances' -> cash_and_bank_balances (added)" in (
        mapped.stdout
    )
    assert loaded.exit_code == 0, loaded.stdout
    assert "acme/acme_ar24.pdf: loaded as doc_id 2 version 2" in loaded.stdout
    # The earlier version and its findings are superseded.
    assert query(root, "acme", "SELECT doc_id, status FROM documents ORDER BY 1") == [
        (1, "superseded"),
        (2, "loaded"),
    ]
    assert query(root, "acme", "SELECT DISTINCT status FROM findings") == [("superseded",)]
    assert query(
        root,
        "acme",
        "SELECT account, value FROM facts WHERE doc_id = 2 AND raw_id IN (SELECT raw_id FROM "
        "raw_values WHERE raw_label = 'Cash & bank balances') ORDER BY value",
    ) == [
        ("cash_and_bank_balances", Decimal("2000000.00")),
        ("cash_and_bank_balances", Decimal("3000000.00")),
    ]
    assert audit_query(
        "SELECT message FROM events WHERE message LIKE 'cash_flow check not run for FY24%'"
    ) == [
        (
            "cash_flow check not run for FY24 standalone: no confirmed cash figure (the balance "
            "sheet shows cash and bank balances, which also hold other bank balances)",
        )
    ]
    assert audit_query(
        "SELECT company, raw_label, account, scope, decision FROM mapping_decisions"
    ) == [("acme", "Cash & bank balances", "cash_and_bank_balances", "company", "confirmed")]
    # Other companies keep the global mapping.
    other = runner.invoke(app, ["ingest", "other", str(path)])
    assert "held as doc_id 1" in other.stdout


def test_map_set_rejects_an_account_of_another_statement(root: Path) -> None:
    result = runner.invoke(
        app,
        ["map", "set", "acme", "-s", "balance_sheet", "-l", "Basic", "-a", "eps_basic"],
    )

    assert result.exit_code == 2
    assert "E304: account 'eps_basic' is a profit_and_loss account" in result.stderr


def test_confirming_a_per_share_label_stores_the_figure_as_printed(
    root: Path, tmp_path: Path
) -> None:
    page = statement(
        "STATEMENT OF PROFIT AND LOSS", ("Earnings per share in rupees", "7.25", "6.5")
    )
    path = write_pdf(tmp_path / "acme_ar25.pdf", page, width=WIDTH)
    assert runner.invoke(app, ["ingest", "acme", str(path)]).exit_code == 0

    result = runner.invoke(app, ["review", "confirm", "acme", "1", "--account", "eps_basic"])

    assert result.exit_code == 0, result.stdout
    assert query(root, "acme", "SELECT fiscal_year, value FROM facts ORDER BY 1") == [
        ("FY24", Decimal("6.50")),
        ("FY25", Decimal("7.25")),
    ]
