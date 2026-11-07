"""`fin ingest` of quarterly and half-year results next to annual reports: periods stay apart,
like periods are compared (E404 finding), half-year balance sheets are checked (E401 finding),
quarterly metrics,
and an account printed on several lines counts once."""

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path
from tests.synthetic_pdf import Page, write_pdf

runner = CliRunner()
UNIT = (50, 65, "(Rs. in Lakhs)")
X = [205, 265, 325, 385, 445, 505]  # column positions, 60 points apart
# Profit and loss lines that add up (subtotal check): PBT = revenue + other income - finance costs
# - depreciation; PAT = PBT - current tax.
LINES = (
    "Revenue from operations",
    "Other income",
    "Finance costs",
    "Depreciation",
    "Profit before tax",
    "Current tax",
    "Profit after tax",
)


def pnl(revenue: int, pat: int) -> list[str]:
    """Figures for LINES with the given revenue and PAT (other income 10, finance costs 20,
    depreciation 40)."""
    pbt = revenue + 10 - 20 - 40
    return [f"{v:,}.00" for v in (revenue, 10, 20, 40, pbt, pbt - pat, pat)]


def results_page(title: str, labels: list[tuple[float, str]], dates: list[str], *columns) -> Page:
    """A results statement: title, unit, length labels (x, text), dates, then LINES with one
    list of figures per column."""
    page: Page = [(50, 50, title), UNIT, *[(x, 80, text) for x, text in labels]]
    page += [(X[i], 92, d) for i, d in enumerate(dates)]
    for n, label in enumerate(LINES):
        y = 110 + 15 * n
        page.append((50, y, label))
        page += [(X[i] + 2, y, figures[n]) for i, figures in enumerate(columns)]
    return page


def q1_results(path: Path, q1_fy26_revenue: int = 1100) -> Path:
    return write_pdf(
        path,
        results_page(
            "Statement of Unaudited Standalone Financial Results for the quarter ended",
            [(258, "Quarter ended"), (385, "Year ended")],
            ["30.06.2025", "31.03.2025", "30.06.2024", "31.03.2025"],
            pnl(q1_fy26_revenue, 800),
            pnl(1050, 750),
            pnl(1000, 700),
            pnl(4100, 2900),
        ),
    )


def q2_results(path: Path, q1_column_revenue: int = 1100, h1_total_assets: str = "500.00") -> Path:
    balance_sheet: Page = [
        (50, 50, "Statement of Assets and Liabilities as at 30.09.2025"),
        UNIT,
        (205, 92, "As at 30.09.2025"),
        (325, 92, "As at 31.03.2025"),
        (50, 110, "Cash and cash equivalents"),
        (225, 110, "50.00"),
        (345, 110, "40.00"),
        (50, 125, "Security deposits"),  # unmapped, so no subtotal check runs
        (225, 125, "450.00"),
        (345, 125, "410.00"),
        (50, 140, "TOTAL ASSETS"),
        (225, 140, h1_total_assets),
        (345, 140, "450.00"),
        (50, 155, "TOTAL EQUITY AND LIABILITIES"),
        (225, 155, "500.00"),
        (345, 155, "450.00"),
    ]
    return write_pdf(
        path,
        results_page(
            "Statement of Unaudited Standalone Financial Results for the quarter and half year",
            [(258, "Quarter ended"), (404, "Half year ended"), (505, "Year ended")],
            ["30.09.2025", "30.06.2025", "30.09.2024", "30.09.2025", "30.09.2024", "31.03.2025"],
            pnl(1210, 900),
            pnl(q1_column_revenue, 800),
            pnl(1020, 720),
            pnl(2310, 1700),
            pnl(2020, 1420),
            pnl(4100, 2900),
        ),
        balance_sheet,
    )


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def ingest(path: Path, company: str = "acme") -> Any:
    return runner.invoke(app, ["ingest", company, str(path)])


def query(root: Path, sql: str, company: str = "acme") -> list[Any]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def findings(root: Path, company: str = "acme") -> list[Any]:
    return query(root, "SELECT code, period, message FROM findings ORDER BY finding_id", company)


def test_quarters_half_years_and_years_load_as_separate_periods(root: Path, tmp_path: Path) -> None:
    assert ingest(q1_results(tmp_path / "q1.pdf")).exit_code == 0
    result = ingest(q2_results(tmp_path / "q2.pdf"))

    assert result.exit_code == 0, result.output
    # One value per account, period and scope: the Q1 column of the Q2 filing does not double
    # the Q1 filing's figure, and the March quarter is not the March year.
    assert query(
        root,
        "SELECT period, period_months, fiscal_year, CAST(value AS VARCHAR) FROM current_facts "
        "WHERE account = 'revenue' ORDER BY period_end, period_months",
    ) == [
        ("FY25-Q1", 3, "FY25", "100000000.00"),
        ("FY25-Q2", 3, "FY25", "102000000.00"),
        ("FY25-H1", 6, "FY25", "202000000.00"),
        ("FY25-Q4", 3, "FY25", "105000000.00"),
        ("FY25", 12, "FY25", "410000000.00"),
        ("FY26-Q1", 3, "FY26", "110000000.00"),
        ("FY26-Q2", 3, "FY26", "121000000.00"),
        ("FY26-H1", 6, "FY26", "231000000.00"),
    ]
    # The Q1 figure comes from the Q1 filing, whose own period it is.
    assert query(
        root,
        "SELECT d.file_name FROM current_facts c JOIN documents d USING (doc_id) "
        "WHERE c.account = 'revenue' AND c.period = 'FY26-Q1'",
    ) == [("q1.pdf",)]
    # The half-year balance sheet balances (E401) and is the FY26-H1 period.
    assert query(
        root,
        "SELECT period, CAST(value AS VARCHAR) FROM current_facts WHERE account = 'total_assets' "
        "ORDER BY period",
    ) == [("FY25", "45000000.00"), ("FY26-H1", "50000000.00")]


def test_quarterly_metrics_year_on_year_and_quarter_on_quarter(root: Path, tmp_path: Path) -> None:
    assert ingest(q1_results(tmp_path / "q1.pdf")).exit_code == 0
    assert ingest(q2_results(tmp_path / "q2.pdf")).exit_code == 0

    metrics = query(
        root,
        "SELECT period, metric, CAST(value AS VARCHAR) FROM metrics "
        "WHERE period IN ('FY26-Q1', 'FY26-Q2', 'FY26-H1') AND category IN ('headline', 'growth') "
        "OR metric IN ('gross_margin', 'ebitda', 'ebitda_margin', 'pat_margin') "
        "AND period IN ('FY26-Q1', 'FY26-Q2', 'FY26-H1') ORDER BY period, metric",
    )

    assert metrics == [
        ("FY26-H1", "ebitda", "231000000.000000"),
        ("FY26-H1", "ebitda_growth", "0.143564"),
        ("FY26-H1", "ebitda_margin", "1.000000"),
        ("FY26-H1", "gross_margin", None),
        ("FY26-H1", "pat", "170000000.000000"),
        ("FY26-H1", "pat_growth", "0.197183"),
        ("FY26-H1", "pat_margin", "0.735931"),
        ("FY26-H1", "revenue", "231000000.000000"),
        ("FY26-H1", "revenue_growth", "0.143564"),
        ("FY26-Q1", "ebitda", "110000000.000000"),
        ("FY26-Q1", "ebitda_growth", "0.100000"),
        ("FY26-Q1", "ebitda_margin", "1.000000"),
        ("FY26-Q1", "gross_margin", None),
        ("FY26-Q1", "pat", "80000000.000000"),
        ("FY26-Q1", "pat_growth", "0.142857"),
        ("FY26-Q1", "pat_margin", "0.727273"),
        ("FY26-Q1", "revenue", "110000000.000000"),
        ("FY26-Q1", "revenue_growth", "0.100000"),
        ("FY26-Q1", "revenue_growth_qoq", "0.047619"),
        ("FY26-Q2", "ebitda", "121000000.000000"),
        ("FY26-Q2", "ebitda_growth", "0.186275"),
        ("FY26-Q2", "ebitda_margin", "1.000000"),
        ("FY26-Q2", "gross_margin", None),
        ("FY26-Q2", "pat", "90000000.000000"),
        ("FY26-Q2", "pat_growth", "0.250000"),
        ("FY26-Q2", "pat_margin", "0.743802"),
        ("FY26-Q2", "revenue", "121000000.000000"),
        ("FY26-Q2", "revenue_growth", "0.186275"),
        ("FY26-Q2", "revenue_growth_qoq", "0.100000"),
    ]
    # Lineage: Q1's quarter-on-quarter growth used Q1 and the March quarter before it.
    assert query(
        root,
        "SELECT input_period, CAST(value AS VARCHAR) FROM metric_inputs "
        "WHERE metric = 'revenue_growth_qoq' AND period = 'FY26-Q1' ORDER BY input_period",
    ) == [("FY25-Q4", "105000000.00"), ("FY26-Q1", "110000000.00")]
    # The annual metrics are unchanged in kind: no quarterly metric names in a fiscal year.
    assert query(
        root, "SELECT DISTINCT metric FROM metrics WHERE period = 'FY25' AND metric LIKE 'revenue%'"
    ) == [("revenue_growth",)]


def q4_results(path: Path) -> Path:
    """The March 2025 quarter's results: Q4 and Q3 of FY25, Q4 of FY24, and both years."""
    return write_pdf(
        path,
        results_page(
            "Statement of Audited Standalone Financial Results for the quarter and year ended",
            [(258, "Quarter ended"), (415, "Year ended")],
            ["31.03.2025", "31.12.2024", "31.03.2024", "31.03.2025", "31.03.2024"],
            pnl(1050, 750),
            pnl(1030, 730),
            pnl(950, 650),
            pnl(4100, 2900),
            pnl(3800, 2600),
        ),
    )


def test_trailing_twelve_months_need_four_consecutive_quarters(root: Path, tmp_path: Path) -> None:
    for path in (q4_results(tmp_path / "q4.pdf"), q1_results(tmp_path / "q1.pdf")):
        assert ingest(path).exit_code == 0
    assert ingest(q2_results(tmp_path / "q2.pdf")).exit_code == 0

    # Quarters loaded: FY24-Q4, FY25-Q1 to Q4, FY26-Q1 and Q2. FY25-Q2 has no FY24-Q3, so no TTM.
    # EBITDA equals revenue in these figures (pnl()).
    assert query(
        root,
        "SELECT period, metric, CAST(value AS VARCHAR) FROM metrics WHERE category = 'ttm' "
        "ORDER BY period_end, metric",
    ) == [
        ("FY25-Q3", "ttm_ebitda", "400000000.000000"),
        ("FY25-Q3", "ttm_ebitda_margin", "1.000000"),
        ("FY25-Q3", "ttm_pat", "280000000.000000"),
        ("FY25-Q3", "ttm_pat_margin", "0.700000"),
        ("FY25-Q3", "ttm_revenue", "400000000.000000"),
        # The four FY25 quarters add up to the year: revenue 4,100 and PAT 2,900 lakhs.
        ("FY25-Q4", "ttm_ebitda", "410000000.000000"),
        ("FY25-Q4", "ttm_ebitda_margin", "1.000000"),
        ("FY25-Q4", "ttm_pat", "290000000.000000"),
        ("FY25-Q4", "ttm_pat_margin", "0.707317"),
        ("FY25-Q4", "ttm_revenue", "410000000.000000"),
        ("FY26-Q1", "ttm_ebitda", "420000000.000000"),
        ("FY26-Q1", "ttm_ebitda_margin", "1.000000"),
        ("FY26-Q1", "ttm_pat", "300000000.000000"),
        ("FY26-Q1", "ttm_pat_margin", "0.714286"),
        ("FY26-Q1", "ttm_revenue", "420000000.000000"),
        ("FY26-Q2", "ttm_ebitda", "439000000.000000"),
        ("FY26-Q2", "ttm_ebitda_margin", "1.000000"),
        ("FY26-Q2", "ttm_pat", "318000000.000000"),
        ("FY26-Q2", "ttm_pat_margin", "0.724374"),
        ("FY26-Q2", "ttm_revenue", "439000000.000000"),
    ]
    # Lineage: FY26-Q2's TTM revenue used Q2 and the three quarters before it.
    assert query(
        root,
        "SELECT input_period, CAST(value AS VARCHAR) FROM metric_inputs "
        "WHERE metric = 'ttm_revenue' AND period = 'FY26-Q2' ORDER BY input_period",
    ) == [
        ("FY25-Q3", "103000000.00"),
        ("FY25-Q4", "105000000.00"),
        ("FY26-Q1", "110000000.00"),
        ("FY26-Q2", "121000000.00"),
    ]


def test_a_different_q1_column_in_the_q2_filing_is_an_e404_finding(
    root: Path, tmp_path: Path
) -> None:
    assert ingest(q1_results(tmp_path / "q1.pdf")).exit_code == 0

    result = ingest(q2_results(tmp_path / "q2.pdf", q1_column_revenue=1110))

    assert result.exit_code == 0, result.output
    assert "q2.pdf: held as doc_id 2" in result.stdout
    # Only the Q1 period is compared with the Q1 filing (revenue, and the PBT and tax it moves).
    rows = findings(root)
    assert {(code, period) for code, period, _ in rows} == {("E404", "FY26-Q1")}
    assert any(
        message.startswith(
            "FY26-Q1 standalone: pbt vs q1.pdf: expected 105000000.00, actual 106000000.00"
        )
        for _, _, message in rows
    )


def test_an_unbalanced_half_year_balance_sheet_is_an_e401_finding(
    root: Path, tmp_path: Path
) -> None:
    result = ingest(q2_results(tmp_path / "q2.pdf", h1_total_assets="505.00"))

    assert result.exit_code == 0, result.output
    assert findings(root)[0] == (
        "E401",
        "FY26-H1",
        "FY26-H1 standalone: total assets vs total equity and liabilities: expected 50000000.00, "
        "actual 50500000.00, difference 500000.00, tolerance 100000.00",
    )


def test_report_says_cleanly_when_there_is_no_annual_data(root: Path, tmp_path: Path) -> None:
    quarters_only = write_pdf(
        tmp_path / "q1.pdf",
        results_page(
            "Statement of Unaudited Standalone Financial Results for the quarter ended",
            [(232, "Quarter ended")],
            ["30.06.2025", "30.06.2024"],
            pnl(1100, 800),
            pnl(1000, 700),
        ),
    )
    assert ingest(quarters_only).exit_code == 0

    result = runner.invoke(app, ["report", "acme", "--scope", "standalone"])

    assert result.exit_code == 2
    assert result.stderr.splitlines()[0] == (
        "E601: acme has no annual standalone data to report on: the health report covers fiscal "
        "years (load an annual report, or results with a 'Year ended' column); quarter and "
        "half-year figures are in `fin sql` and the dashboard"
    )


def test_a_workbook_with_quarter_columns(root: Path, tmp_path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Profit and Loss"
    sheet.append(["Standalone Statement of Profit and Loss"])
    sheet.append(["(₹ in Lakhs)"])
    sheet.append(
        ["Particulars", "Quarter ended 30.06.2025", "Quarter ended 30.06.2024", "31.03.2025"]
    )
    sheet.append(["Revenue from operations", 1100, 1000, 4100])
    path = tmp_path / "q1.xlsx"
    book.save(path)

    assert ingest(path).exit_code == 0

    assert query(
        root,
        "SELECT period, period_months, CAST(value AS VARCHAR) FROM facts ORDER BY fact_id",
    ) == [
        ("FY26-Q1", 3, "110000000.00"),
        ("FY25-Q1", 3, "100000000.00"),
        ("FY25", 12, "410000000.00"),
    ]


def balance_sheet(path: Path, *lines: tuple[str, str, str]) -> Path:
    page: Page = [
        (50, 50, "STANDALONE BALANCE SHEET"),
        UNIT,
        (380, 85, "31 March 2025"),
        (470, 85, "31 March 2024"),
    ]
    for n, (label, fy25, fy24) in enumerate(lines):
        y = 105 + 15 * n
        page += [(50, y, label), (390, y, fy25), (480, y, fy24)]
    return write_pdf(path, page)


@pytest.mark.parametrize(
    "lines",
    [
        # Two lines for one account: their sum.
        [("Sundry creditors", "12.00", "10.00"), ("Trade payables", "18.00", "15.00")],
        # A total line above its two parts: the total, not the total plus the parts.
        [
            ("(b) Trade payables", "30.00", "25.00"),
            ("Sundry creditors", "12.00", "10.00"),
            ("Trade payables", "18.00", "15.00"),
        ],
    ],
)
def test_an_account_on_several_lines_counts_once(
    root: Path, tmp_path: Path, lines: list[tuple[str, str, str]]
) -> None:
    assert ingest(balance_sheet(tmp_path / "ar.pdf", *lines)).exit_code == 0

    rows = query(
        root,
        "SELECT period, CAST(value AS VARCHAR), len(fact_ids) FROM current_facts "
        "WHERE account = 'trade_payables' ORDER BY period",
    )

    used = 2 if len(lines) == 2 else 1
    assert rows == [("FY24", "2500000.00", used), ("FY25", "3000000.00", used)]
