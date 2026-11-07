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


# The latest fiscal year, standalone (the document's only scope), in the document's unit (lakh).
# Exceptional items are a gain the company adds (22 + 3 = 25), so profit before tax is explained.
EXPECTED_FY25 = """\
acme standalone FY25: profit and loss (₹ in lakh)
  Revenue from operations                                                  100.00
  Other income                                                               2.00
Total income                                                               102.00
  Cost of materials consumed                                                60.00
  Employee benefits expense                                                 20.00
Total expenses                                                              80.00
Profit before exceptional items and tax                                     22.00
  Exceptional items                                                          3.00
Profit before tax                                                           25.00
  Current tax                                                                5.00
  Deferred tax expense                                                       1.00
Total tax expense                                                            6.00
Profit for the year                                                         19.00
  Other comprehensive income items                                          -0.75
Other comprehensive income for the year                                     -0.75
Total comprehensive income for the year                                     18.25
Basic earnings per share                                                    12.34
Diluted earnings per share                                                  12.30

acme standalone FY25: balance sheet (₹ in lakh)
    Property, plant and equipment, net block                                60.00
    Unmapped / other                                                         5.00
  Total non-current assets                                                  65.00  \
(lines explain 60.00, 92.31%)
    Inventories                                                             10.00
    Cash and cash equivalents                                               25.00
  Total current assets                                                      35.00
Total assets                                                               100.00
    Equity share capital                                                    10.00
    Other equity                                                            50.00
  Total equity                                                              60.00
    Non-current borrowings                                                  15.00
  Total non-current liabilities                                             15.00
    Current borrowings                                                      10.00
    Trade payables                                                          15.00
  Total current liabilities                                                 25.00
Total equity and liabilities                                               100.00"""


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
    assert (total["unit"], by_label[("profit_and_loss", "Basic earnings per share")]["unit"]) == (
        "lakh",
        "rupees_per_share",
    )
    assert {row["period"] for row in rows} == {"FY24"}


def test_standard_statements_view_is_in_fin_query(root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "sql",
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
        ["Line (₹ in lakh)", "FY24", "FY25"],
        ["Property, plant and equipment, net block", 50, 60],
        ["Unmapped / other", 5, 5],
        ["Total non-current assets", 55, 65],
    ]
    assert sheet["A2"].alignment.indent == 2

    metrics = book["Metrics"]
    header = [cell.value for cell in metrics[1]]
    assert header == ["Scope", "Category", "Metric", "Unit", "FY24", "FY25", "Definition"]
    table = {row[2]: row for row in metrics.iter_rows(min_row=2, values_only=True)}
    assert table["roe"][:6] == ("standalone", "returns", "roe", "ratio", 0.36, 0.316667)
    assert table["eps_basic"][3:6] == ("rupees_per_share", 11, 12.34)
    assert table["net_debt"][3:6] == ("₹ in lakh", 2, 0)  # FY24: 20 + 8 - 26; FY25: 15 + 10 - 25
    assert table["roe"][6] == "Return on equity on the closing balance sheet."


def test_statements_of_an_unknown_company_is_e601(root: Path) -> None:
    result = runner.invoke(app, ["statements", "nobody"])

    assert result.exit_code == 2
    assert "no database for nobody" in result.output


def test_metrics_for_per_share_figures_exceptional_items_and_tax(root: Path) -> None:
    result = runner.invoke(
        app,
        [
            "sql",
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


# Finance costs and depreciation, and exceptional items both ways: FY25 a gain the company adds
# (22 + 3 = 25), FY24 a charge it subtracts (20 - 2 = 18). Rs. in lakhs.
PROFIT_AND_LOSS_WITH_EXCEPTIONAL_ITEMS = statement(
    "STATEMENT OF PROFIT AND LOSS",
    ("Revenue from operations", "100.00", "90.00"),
    ("Other income", "2.00", "1.00"),
    ("Total income", "102.00", "91.00"),
    ("Cost of materials consumed", "50.00", "45.00"),
    ("Employee benefits expense", "20.00", "18.00"),
    ("Finance costs", "4.00", "3.00"),
    ("Depreciation and amortisation expense", "6.00", "5.00"),
    ("Total expenses", "80.00", "71.00"),
    ("Profit before exceptional items and tax", "22.00", "20.00"),
    ("Exceptional items", "3.00", "2.00"),
    ("Profit before tax", "25.00", "18.00"),
    ("Current tax", "6.00", "4.00"),
    ("Total tax expense", "6.00", "4.00"),
    ("Profit for the year", "19.00", "14.00"),
)


def test_ebitda_leaves_out_exceptional_items(data_root: Path, tmp_path: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    path = write_pdf(
        tmp_path / "zeta_ar25.pdf",
        BALANCE_SHEET,
        PROFIT_AND_LOSS_WITH_EXCEPTIONAL_ITEMS,
        width=WIDTH,
    )
    ingest = runner.invoke(app, ["ingest", "zeta", str(path)])
    assert ingest.exit_code == 0, ingest.output

    sql = (
        "SELECT period, metric, value FROM metrics WHERE metric IN "
        "('ebitda', 'ebitda_margin', 'ebitda_growth', 'interest_cover', 'ebit') "
        "ORDER BY period, metric"
    )
    result = runner.invoke(app, ["sql", "zeta", "--question", "EBITDA", "--sql", sql])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["rows"]
    values = [(r["period"], r["metric"], r["value"] and Decimal(r["value"])) for r in rows]
    assert values == [
        ("FY24", "ebit", Decimal("2100000")),  # PBT 18 + finance costs 3 (lakhs)
        ("FY24", "ebitda", Decimal("2700000")),  # 20 + 3 + 5 - 1, not 18 + 3 + 5 - 1
        ("FY24", "ebitda_growth", None),  # no FY23
        ("FY24", "ebitda_margin", Decimal("0.3")),  # 27 / 90
        ("FY24", "interest_cover", Decimal("9")),  # 27 / 3
        ("FY25", "ebit", Decimal("2900000")),  # 25 + 4
        ("FY25", "ebitda", Decimal("3000000")),  # 22 + 4 + 6 - 2, not 25 + 4 + 6 - 2
        ("FY25", "ebitda_growth", Decimal("0.111111")),  # 30 / 27 - 1
        ("FY25", "ebitda_margin", Decimal("0.3")),  # 30 / 100
        ("FY25", "interest_cover", Decimal("7.5")),  # 30 / 4
    ]

    lineage = runner.invoke(
        app,
        [
            "sql",
            "zeta",
            "--question",
            "EBITDA inputs",
            "--sql",
            "SELECT account, fact_id IS NOT NULL AS found FROM metric_inputs "
            "WHERE metric = 'ebitda' AND period = 'FY25' ORDER BY account",
        ],
    )
    assert lineage.exit_code == 0, lineage.output
    assert json.loads(lineage.stdout)["rows"] == [
        {"account": "depreciation", "found": True},
        {"account": "finance_cost", "found": True},
        {"account": "other_income", "found": True},
        {"account": "profit_before_exceptional_items", "found": True},
        {"account": "share_of_associates_jv", "found": False},  # none: PBT has nothing to add
    ]
