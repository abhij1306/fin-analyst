"""`fin statements` and `fin export`: the standard statements of a synthetic annual report (PDF),
with an "Unmapped / other" line where a printed subtotal is more than its mapped lines, and the
Excel workbook of every period side by side."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from fin.cli import app
from tests.integration.test_real_report_mapping import PROFIT_AND_LOSS, WIDTH, statement
from tests.synthetic_pdf import write_pdf

runner = CliRunner()

BALANCE_SHEET = statement(
    "BALANCE SHEET",
    "Non-current assets",
    ("Property, plant and equipment", "60.00", "50.00"),
    ("Security deposits", "5.00", "5.00"),  # no seed mapping: left to review
    ("Total non-current assets", "65.00", "55.00"),
    "Current assets",
    ("Inventories", "10.00", "9.00"),
    ("Cash and cash equivalents", "25.00", "26.00"),
    ("Total current assets", "35.00", "35.00"),
    ("TOTAL ASSETS", "100.00", "90.00"),
    "EQUITY AND LIABILITIES",
    ("Equity share capital", "10.00", "10.00"),
    ("Other equity", "50.00", "40.00"),
    ("Total equity", "60.00", "50.00"),
    "Non-current liabilities",
    ("Borrowings", "15.00", "20.00"),
    ("Total non-current liabilities", "15.00", "20.00"),
    "Current liabilities",
    ("Borrowings", "10.00", "8.00"),
    ("Trade payables", "15.00", "12.00"),
    ("Total current liabilities", "25.00", "20.00"),
    ("TOTAL EQUITY AND LIABILITIES", "100.00", "90.00"),
)


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    path = write_pdf(tmp_path / "acme_ar25.pdf", BALANCE_SHEET, PROFIT_AND_LOSS, width=WIDTH)
    result = runner.invoke(app, ["ingest", "acme", str(path)])
    assert result.exit_code == 0, result.output
    assert "loaded as doc_id 1" in result.stdout
    return data_root


def test_statements_show_the_standard_order_and_what_the_lines_do_not_explain(root: Path) -> None:
    result = runner.invoke(app, ["statements", "acme"])

    assert result.exit_code == 0, result.output
    assert result.stdout == EXPECTED_FY25 + "\n"


# The latest fiscal year, standalone (the document's only scope). Rs. in lakhs, shown in rupees.
# Exceptional items are a gain the company adds (22 + 3 = 25), so profit before tax is explained.
EXPECTED_FY25 = """\
acme standalone FY25: profit and loss (rupees)
  Revenue from operations                                           10,000,000.00
  Other income                                                         200,000.00
Total income                                                        10,200,000.00
  Cost of materials consumed                                         6,000,000.00
  Employee benefits expense                                          2,000,000.00
Total expenses                                                       8,000,000.00
Profit before exceptional items and tax                              2,200,000.00
  Exceptional items                                                    300,000.00
Profit before tax                                                    2,500,000.00
  Current tax                                                          500,000.00
  Deferred tax expense                                                 100,000.00
Total tax expense                                                      600,000.00
Profit for the year                                                  1,900,000.00
  One line of other comprehensive income                               -75,000.00
Other comprehensive income for the year                                -75,000.00
Total comprehensive income for the year                              1,825,000.00
Basic earnings per share                                                    12.34
Diluted earnings per share                                                  12.30

acme standalone FY25: balance sheet (rupees)
    Property, plant and equipment, net block                         6,000,000.00
    Unmapped / other                                                   500,000.00
  Total non-current assets                                           6,500,000.00  \
(lines explain 6,000,000.00, 92.31%)
    Inventories                                                      1,000,000.00
    Cash and cash equivalents                                        2,500,000.00
  Total current assets                                               3,500,000.00
Total assets                                                        10,000,000.00
    Equity share capital                                             1,000,000.00
    Other equity                                                     5,000,000.00
  Total equity                                                       6,000,000.00
    Non-current borrowings                                           1,500,000.00
  Total non-current liabilities                                      1,500,000.00
    Current borrowings                                               1,000,000.00
    Trade payables                                                   1,500,000.00
  Total current liabilities                                          2,500,000.00
Total equity and liabilities                                        10,000,000.00"""


def test_statements_json_has_coverage_and_lineage(root: Path) -> None:
    result = runner.invoke(
        app, ["statements", "acme", "--period", "FY24", "--scope", "standalone", "--json"]
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    by_label = {(row["statement"], row["label"]): row for row in rows}
    total = by_label[("balance_sheet", "Total non-current assets")]
    other = by_label[("balance_sheet", "Unmapped / other")]
    assert (total["value"], total["explained"], total["coverage"]) == (
        "5500000.00",
        "5000000.00",
        "0.909091",
    )
    assert (other["line_type"], other["account"], other["value"], other["depth"]) == (
        "other",
        None,
        "500000.00",
        2,
    )
    assert total["fact_id"] is not None and other["fact_id"] is None
    assert {row["period"] for row in rows} == {"FY24"}


def test_standard_statements_view_is_in_fin_query(root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "query",
            "acme",
            "--question",
            "What is not explained?",
            "--sql",
            "SELECT period, label, value FROM standard_statements WHERE line_type = 'other' "
            "ORDER BY period",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["rows"]
    assert rows == [
        {"period": "FY24", "label": "Unmapped / other", "value": "500000.00"},
        {"period": "FY25", "label": "Unmapped / other", "value": "500000.00"},
    ]


def test_export_writes_one_sheet_per_statement_and_a_metrics_sheet(root: Path) -> None:
    result = runner.invoke(app, ["export", "acme"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "exported reports/acme/acme_financials.xlsx\n"
    book = load_workbook(root / "reports" / "acme" / "acme_financials.xlsx")
    assert book.sheetnames == ["P&L standalone", "Balance sheet standalone", "Metrics"]

    sheet = book["Balance sheet standalone"]
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    assert rows[:4] == [
        ["Line", "FY24", "FY25"],
        ["Property, plant and equipment, net block", 5000000, 6000000],
        ["Unmapped / other", 500000, 500000],
        ["Total non-current assets", 5500000, 6500000],
    ]
    assert sheet["A2"].alignment.indent == 2

    metrics = book["Metrics"]
    header = [cell.value for cell in metrics[1]]
    assert header == ["Scope", "Category", "Metric", "Unit", "FY24", "FY25", "Definition"]
    table = {row[2]: row for row in metrics.iter_rows(min_row=2, values_only=True)}
    assert table["roe"][:6] == ("standalone", "returns", "roe", "ratio", 0.36, 0.316667)
    assert table["eps_basic"][4:6] == (11, 12.34)
    assert table["roe"][6] == "Return on equity on the closing balance sheet."


def test_statements_of_an_unknown_company_is_e601(root: Path) -> None:
    result = runner.invoke(app, ["statements", "nobody"])

    assert result.exit_code == 2
    assert "no database for nobody" in result.output


def test_metrics_for_per_share_figures_exceptional_items_and_tax(root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "query",
            "acme",
            "--question",
            "Per share and earnings quality",
            "--sql",
            "SELECT period, metric, value FROM metrics WHERE category IN "
            "('per_share', 'earnings_quality') ORDER BY period, metric",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["rows"]
    assert [(r["period"], r["metric"], Decimal(r["value"])) for r in rows] == [
        ("FY24", "effective_tax_rate", Decimal("0.217391")),  # 5 / 23
        ("FY24", "eps_basic", Decimal("11")),
        ("FY24", "eps_diluted", Decimal("11")),
        ("FY24", "exceptional_items_to_pbt", Decimal("0")),  # printed as nil
        ("FY24", "other_income_to_pbt", Decimal("0.043478")),
        ("FY25", "effective_tax_rate", Decimal("0.24")),  # 6 / 25
        ("FY25", "eps_basic", Decimal("12.34")),
        ("FY25", "eps_diluted", Decimal("12.30")),
        ("FY25", "exceptional_items_to_pbt", Decimal("0.12")),  # 3 / 25, as printed
        ("FY25", "other_income_to_pbt", Decimal("0.08")),
    ]
