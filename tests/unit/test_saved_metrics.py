"""Saved metrics (fin.metrics.saved, `fin metric`): computed with the built-in metrics, in
metric_definitions as 'user', traced in metric_inputs, and in the Excel export and the analytics
tables the dashboards read (SPEC-07 criterion 3)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from fin.audit.errors import SavedMetricInvalid, SavedMetricUnknown, SqlFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.metrics.export import refresh_analytics
from fin.metrics.saved import add_metric, list_metrics, remove_metric
from fin.paths import analytics_db_path, audit_db_path, company_db_path
from fin.query.write import write_sql
from fin.workbook import export_workbook
from tests.unit.question_fixtures import add_company, copy_root, make_template


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_template(tmp_path_factory)


@pytest.fixture
def root(data_root: Path, template: Path) -> Path:
    return copy_root(template, data_root)


def run(root: Path) -> Run:
    return Run("metric", {}, root)


def rows(root: Path, sql: str, company: str = "acme") -> list[Any]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def value(root: Path, metric: str, company: str = "acme") -> Any:
    found = rows(root, f"SELECT value FROM metrics WHERE metric = '{metric}'", company)
    return found[0][0] if found else "absent"


def test_a_saved_metric_is_in_metrics_definitions_and_inputs(root: Path) -> None:
    result = add_metric(
        run(root), "cfo_to_revenue", "cfo / revenue", "ratio", "Cash per revenue.", "acme"
    )

    assert result["periods"] == "annual"  # cfo is an annual metric
    assert rows(
        root,
        "SELECT period, scope, value, unit, category FROM metrics WHERE metric = 'cfo_to_revenue'",
    ) == [("FY25", "standalone", Decimal("0.250000"), "ratio", "user")]
    assert rows(
        root,
        "SELECT category, unit, definition, formula FROM metric_definitions "
        "WHERE metric = 'cfo_to_revenue'",
    ) == [("user", "ratio", "Cash per revenue.", "cfo / revenue")]
    assert rows(
        root,
        "SELECT account, fact_id, value FROM metric_inputs "
        "WHERE metric = 'cfo_to_revenue' ORDER BY account",
    ) == [("cfo", 2, Decimal("250.00")), ("revenue", 1, Decimal("1000.00"))]
    assert value(root, "ebitda_margin") is None  # the built-in metrics are unchanged
    assert value(root, "receivable_days") == Decimal("73.000000")


def test_accounts_numbers_and_brackets_and_a_missing_input(root: Path) -> None:
    add_metric(
        run(root), "debtor_days", "trade_receivables * 365 / revenue", "days", "Days.", "acme"
    )
    add_metric(run(root), "gross", "(revenue - cost_of_materials) / revenue", "ratio", "G.", "acme")
    add_metric(
        run(root), "rec_less_pay", "trade_receivables - trade_payables", "rupees", "R.", "acme"
    )
    add_metric(run(root), "neg", "-cfo / (revenue - 1000)", "ratio", "Zero divisor.", "acme")

    assert value(root, "debtor_days") == Decimal("73.000000")
    assert value(root, "gross") == Decimal("0.400000")
    assert value(root, "rec_less_pay") is None  # trade payables not printed: no value, not 200
    assert value(root, "neg") is None  # division by zero
    assert rows(
        root,
        "SELECT fact_id FROM metric_inputs WHERE metric = 'rec_less_pay' "
        "AND account = 'trade_payables'",
    ) == [(None,)]


def test_a_saved_metric_can_use_one_saved_before_it(root: Path) -> None:
    add_metric(run(root), "cash_conv", "cfo / revenue", "ratio", "C.", "acme")
    result = add_metric(run(root), "cash_conv_pct", "cash_conv * 100", "ratio", "C%.", "acme")

    assert result["uses"] == ["cash_conv"]
    assert value(root, "cash_conv_pct") == Decimal("25.000000")
    with pytest.raises(SavedMetricUnknown) as error:
        remove_metric(run(root), "cash_conv", "acme")
    assert str(error.value) == "E507: cash_conv is used by cash_conv_pct; remove those first"


def test_it_reaches_the_excel_export_and_the_dashboard_tables(root: Path) -> None:
    add_metric(run(root), "cfo_to_revenue", "cfo / revenue", "ratio", "Cash per revenue.", "acme")
    ensure_database(audit_db_path(root), "audit")

    refresh_analytics(run(root))
    book = load_workbook(export_workbook(root, "acme"))

    with connect(analytics_db_path(root)) as con:
        assert con.execute(
            "SELECT company, period, value, category FROM metrics WHERE metric = 'cfo_to_revenue'"
        ).fetchall() == [("acme", "FY25", Decimal("0.250000"), "user")]
        assert con.execute(
            "SELECT formula FROM metric_definitions WHERE metric = 'cfo_to_revenue'"
        ).fetchall() == [("cfo / revenue",)]
    sheet = book["Metrics"]
    [line] = [r for r in sheet.iter_rows(values_only=True) if "cfo_to_revenue" in r]
    assert line[1] == "user"
    assert 0.25 in line


def test_a_shared_metric_reaches_every_company_and_a_later_one(root: Path) -> None:
    add_company(root, "bravo")
    result = add_metric(run(root), "cfo_to_revenue", "cfo / revenue", "ratio", "C.", None)
    add_company(root, "charlie")

    assert result["companies"] == ["acme", "bravo"]
    for company in ("acme", "bravo", "charlie"):
        assert value(root, "cfo_to_revenue", company) == Decimal("0.250000"), company
    assert [m["metric"] for m in list_metrics(root, None)] == ["cfo_to_revenue"]
    assert [(m["metric"], m["shared"]) for m in list_metrics(root, "charlie")] == [
        ("cfo_to_revenue", True)
    ]

    remove_metric(run(root), "cfo_to_revenue", None)

    assert value(root, "cfo_to_revenue", "bravo") == "absent"
    assert list_metrics(root, None) == []


def test_removing_a_company_metric(root: Path) -> None:
    add_metric(run(root), "cfo_to_revenue", "cfo / revenue", "ratio", "C.", "acme")

    remove_metric(run(root), "cfo_to_revenue", "acme")

    assert value(root, "cfo_to_revenue") == "absent"
    assert rows(root, "SELECT count(*) FROM metric_definitions WHERE category = 'user'") == [(0,)]


@pytest.mark.parametrize(
    ("name", "formula", "unit", "message"),
    [
        ("Bad Name", "cfo", "ratio", "E506: 'Bad Name' is not a metric name"),
        ("revenue", "cfo", "ratio", "E506: revenue is already a metric or an account"),
        ("trade_payables", "cfo", "ratio", "E506: trade_payables is already a metric"),
        ("x", "cfo", "percent", "E506: unknown unit 'percent'"),
        ("x", "cfo / nothing", "ratio", "'cfo / nothing' cannot be used: nothing is not"),
        ("x", "cfo; DROP TABLE facts", "ratio", "cannot be used: ';' is not allowed"),
        ("x", "(cfo / revenue", "ratio", "a bracket is not closed"),
        ("x", "cfo revenue", "ratio", "unexpected 'revenue'"),
        ("x", "cfo /", "ratio", "it ends too early"),
    ],
)  # fmt: skip
def test_what_cannot_be_saved(root: Path, name: str, formula: str, unit: str, message: str) -> None:
    with pytest.raises(SavedMetricInvalid) as error:
        add_metric(run(root), name, formula, unit, "D.", "acme")

    assert message in str(error.value)
    assert list_metrics(root, "acme") == []


def test_removing_an_unknown_metric(root: Path) -> None:
    with pytest.raises(SavedMetricUnknown) as error:
        remove_metric(run(root), "nothing", "acme")

    assert str(error.value) == "E507: no metric nothing saved for acme (`uv run fin metric list`)"


def test_sql_write_does_not_change_saved_metrics(root: Path) -> None:
    with pytest.raises(SqlFailed) as error:
        write_sql(run(root), "acme", "DELETE FROM saved_metrics")

    assert "use `fin metric add` or `fin metric remove`" in str(error.value)
