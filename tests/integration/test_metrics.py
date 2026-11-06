"""Phase 7: metric views on the synthetic documents, checked against the answer key, and the
analytics export (`fin metrics refresh`, and after every ingest)."""

import shutil
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.paths import analytics_db_path, company_db_path

runner = CliRunner()
SAMPLES = Path(__file__).parents[1] / "fixtures" / "sample-financials"

# Metrics the answer key gives that must be NULL here: their inputs are in no document. The key
# computes them from the generator's model.
#  - revenue_growth in the first year: the prior year's revenue is not in any document.
NOT_IN_DOCUMENTS = {
    ("kestrel", "standalone", "FY23", "revenue_growth"),
    ("tarang", "standalone", "FY23", "revenue_growth"),
}


@pytest.fixture(scope="module")
def ingested(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A data folder with every clean synthetic document ingested (shared, do not change it)."""
    root = (tmp_path_factory.mktemp("metrics") / "FinAnalysis").resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIN_DATA_DIR", str(root))
        assert runner.invoke(app, ["init"]).exit_code == 0
        for company in ("kestrel", "bluepeak", "tarang"):
            result = runner.invoke(app, ["ingest", company, str(SAMPLES / "clean" / company)])
            assert result.exit_code == 0, result.output
        # The last ingest exported all three companies.
        assert result.stdout.splitlines()[-1] == (
            "metrics: shared/analytics.duckdb refreshed, 3 companies, 252 metric rows"
        )
    yield root


@pytest.fixture
def root(ingested: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of the ingested data folder, for tests that change it."""
    copy = tmp_path / "FinAnalysis"
    shutil.copytree(ingested, copy)
    monkeypatch.setenv("FIN_DATA_DIR", str(copy))
    return copy


def query(path: Path, sql: str, params: list[Any] | None = None) -> list[Any]:
    with connect(path) as con:
        return con.execute(sql, params or []).fetchall()


def expected(answer_key: dict[str, Any], company: str, metric: str, value: float) -> Decimal:
    """The key's value in our precision: money in rupees (the key gives it in hundredths of a
    document unit), ratios and days rounded half up to 6 decimal places."""
    if metric in ("ebitda", "free_cash_flow"):
        per_unit = answer_key["companies"][company]["rupees_per_document_unit"]
        return Decimal(value) * per_unit / 100
    return Decimal(repr(value)).quantize(Decimal("0.000001"), ROUND_HALF_UP)


@pytest.mark.parametrize("company", ["kestrel", "bluepeak", "tarang"])
def test_metrics_equal_the_answer_key(
    ingested: Path, answer_key: dict[str, Any], company: str
) -> None:
    rows = query(
        company_db_path(ingested, company),
        "SELECT scope, fiscal_year, metric, value FROM metrics",
    )
    got = {(scope, year, metric): value for scope, year, metric, value in rows}
    key = answer_key["companies"][company]["metrics"]
    # Every company, scope and year in the key has metrics, and nothing else does.
    assert {(s, y) for s, y, _ in got} == {(s, y) for s in key for y in key[s]}
    for scope, years in key.items():
        for year, metrics in years.items():
            for metric, value in metrics.items():
                want = None
                if (company, scope, year, metric) not in NOT_IN_DOCUMENTS:
                    want = expected(answer_key, company, metric, value)
                assert got[(scope, year, metric)] == want, (company, scope, year, metric)


def test_metrics_not_in_the_key(ingested: Path) -> None:
    rows = query(
        company_db_path(ingested, "kestrel"),
        "SELECT metric, value, unit FROM metrics WHERE fiscal_year = 'FY23' "
        "AND metric IN ('gross_margin', 'capex_to_revenue', 'roce', 'roe') ORDER BY metric",
    )
    assert rows == [
        ("capex_to_revenue", Decimal("0.080000"), "ratio"),
        ("gross_margin", Decimal("0.420000"), "ratio"),
        ("roce", Decimal("0.211463"), "ratio"),
        ("roe", Decimal("0.203347"), "ratio"),
    ]
    # Bluepeak FY22 is its earliest year: no growth, and the key has none either.
    assert query(
        company_db_path(ingested, "bluepeak"),
        "SELECT scope, value FROM metrics WHERE fiscal_year = 'FY22' "
        "AND metric = 'revenue_growth' ORDER BY scope",
    ) == [("consolidated", None), ("standalone", None)]


def test_metric_inputs_trace_each_metric_to_its_facts(ingested: Path) -> None:
    kestrel = company_db_path(ingested, "kestrel")
    rows = query(
        kestrel,
        "SELECT i.account, i.input_fiscal_year, r.sheet || '!' || r.cell, i.value "
        "FROM metric_inputs i JOIN facts f USING (fact_id) JOIN raw_values r USING (raw_id) "
        "WHERE i.metric = 'revenue_growth' AND i.fiscal_year = 'FY24' ORDER BY 2",
    )
    assert rows == [
        ("revenue", "FY23", "Profit and Loss!E7", Decimal("1059520000.00")),
        ("revenue", "FY24", "Profit and Loss!D7", Decimal("1197258000.00")),
    ]
    # A missing input shows as a row without a fact.
    assert query(
        kestrel,
        "SELECT account, input_fiscal_year, fact_id FROM metric_inputs "
        "WHERE metric = 'revenue_growth' AND fiscal_year = 'FY23' ORDER BY 2",
    ) == [("revenue", "FY22", None), ("revenue", "FY23", 3)]
    # Bluepeak prints no total current assets or liabilities, so the ratio sums the lines.
    assert query(
        company_db_path(ingested, "bluepeak"),
        "SELECT account, stands_in_for, fact_id IS NOT NULL FROM metric_inputs "
        "WHERE metric = 'current_ratio' AND fiscal_year = 'FY25' AND scope = 'standalone' "
        "ORDER BY stands_in_for NULLS FIRST, account",
    ) == [
        ("total_current_assets", None, False),
        ("total_current_liabilities", None, False),
        ("cash", "total_current_assets", True),
        ("inventories", "total_current_assets", True),
        ("other_current_assets", "total_current_assets", True),
        ("trade_receivables", "total_current_assets", True),
        ("other_current_liabilities", "total_current_liabilities", True),
        ("st_borrowings", "total_current_liabilities", True),
        ("trade_payables", "total_current_liabilities", True),
    ]


def test_metrics_refresh_rebuilds_analytics(root: Path) -> None:
    analytics = analytics_db_path(root)
    # Simulate a label taken back to review: kestrel FY25 revenue no longer confirmed.
    with connect(company_db_path(root, "kestrel"), write=True) as con:
        con.execute(
            "UPDATE facts SET confirmed = false WHERE account = 'revenue' AND fiscal_year = 'FY25'"
        )

    result = runner.invoke(app, ["metrics", "refresh"])

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "metrics: shared/analytics.duckdb refreshed, 3 companies, 252 metric rows\n"
    )
    assert query(
        analytics,
        "SELECT company, documents, facts, unconfirmed_facts, failed_validations FROM companies "
        "ORDER BY company",
    ) == [("bluepeak", 3, 600, 0, 0), ("kestrel", 1, 162, 1, 0), ("tarang", 2, 216, 0, 0)]
    assert query(
        analytics,
        "SELECT company, scope, count(*), count(value) FROM metrics GROUP BY ALL ORDER BY ALL",
    ) == [
        ("bluepeak", "consolidated", 72, 71),
        ("bluepeak", "standalone", 72, 71),
        ("kestrel", "standalone", 54, 46),
        ("tarang", "standalone", 54, 53),
    ]
    # Unconfirmed revenue makes every FY25 metric that needs it NULL; the rest are unchanged.
    assert query(
        analytics,
        "SELECT metric, value FROM metrics WHERE company = 'kestrel' AND fiscal_year = 'FY25' "
        "AND metric IN ('revenue_growth', 'ebitda_margin', 'receivable_days', 'payable_days') "
        "ORDER BY metric",
    ) == [
        ("ebitda_margin", None),
        ("payable_days", Decimal("60.000025")),
        ("receivable_days", None),
        ("revenue_growth", None),
    ]


def test_superseded_documents_are_left_out(root: Path) -> None:
    source = SAMPLES / "clean" / "tarang" / "Tarang_Industrial_Services_Annual_Report_FY2024-25.pdf"
    result = runner.invoke(app, ["ingest", "tarang", str(source), "--force"])
    assert result.exit_code == 0, result.output
    tarang = company_db_path(root, "tarang")
    # The forced copy supersedes the first; FY25 inputs come from the new version only.
    [(new_doc,)] = query(tarang, "SELECT doc_id FROM documents WHERE version = 2")
    assert query(
        tarang,
        "SELECT DISTINCT doc_id FROM metric_inputs "
        "WHERE input_fiscal_year = 'FY25' AND fact_id IS NOT NULL",
    ) == [(new_doc,)]
    assert query(
        analytics_db_path(root),
        "SELECT value FROM metrics WHERE company = 'tarang' AND fiscal_year = 'FY25' "
        "AND metric = 'receivable_days'",
    ) == [(Decimal("74.999878"),)]


def test_failed_validations_are_counted(root: Path) -> None:
    broken = SAMPLES / "broken" / "Kestrel_UNBALANCED_balance_sheet.xlsx"
    result = runner.invoke(app, ["ingest", "kestrel", str(broken)])

    assert result.exit_code == 1, result.output
    assert query(
        analytics_db_path(root),
        "SELECT company, documents, failed_validations FROM companies ORDER BY company",
    ) == [("bluepeak", 3, 0), ("kestrel", 1, 4), ("tarang", 2, 0)]
