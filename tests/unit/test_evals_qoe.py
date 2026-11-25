"""Golden evaluation of working capital (kinds nwc_year_end and nwc_monthly) on the synthetic
engagement of tests/qoe_fixtures.py: a figure a person read passes, a planted wrong one fails, and
`fin eval init` lists every line with its source."""

import csv
from pathlib import Path

import pytest

from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from tests.qoe_fixtures import build_root, run


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_root(tmp_path_factory.mktemp("qoe-eval"))


def golden(root: Path, *lines: tuple[str, str, str, str]) -> None:
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for kind, period, ledger, expected in lines:
            writer.writerow((kind, period, "", "", ledger, expected, "rupees", "file", "yes"))


def outcomes(root: Path) -> list[str]:
    return [c["outcome"] for c in run_eval(run(root), "acme")["checks"]]


def test_a_working_capital_figure_checked_against_the_source_passes(root: Path) -> None:
    golden(
        root,
        ("nwc_year_end", "FY25", "net_working_capital", "564,000.00"),
        ("nwc_year_end", "FY26-YTD-SEP", "trade_payables", "250,000"),
        ("nwc_monthly", "2024-05-31", "net_working_capital", "694,000"),
        ("nwc_monthly", "2024-04-30", "trade_receivables", "150,000"),
    )
    assert outcomes(root) == ["pass"] * 4


def test_a_wrong_figure_fails_and_a_month_fin_does_not_have_is_missing(root: Path) -> None:
    golden(
        root,
        ("nwc_year_end", "FY25", "net_working_capital", "564,001.00"),
        ("nwc_monthly", "2024-05-31", "net_working_capital", "694,001"),
        ("nwc_monthly", "2023-05-31", "net_working_capital", "1"),
    )
    assert outcomes(root) == ["fail", "fail", "missing"]


def test_init_lists_every_working_capital_line_with_its_source(root: Path) -> None:
    golden_path(root, "acme").unlink(missing_ok=True)
    init_golden(run(root), "acme")
    with golden_path(root, "acme").open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["kind"].startswith("nwc_")]
    year_end = [r for r in rows if r["kind"] == "nwc_year_end"]
    monthly = [r for r in rows if r["kind"] == "nwc_monthly"]
    assert len(year_end) == 3 * 6 and len(monthly) == 18 * 6
    assert {r["source"] for r in year_end} == {"tb24.xlsx", "tb25.xlsx", "tb-stub.xlsx"}
    assert all(
        "lv.xlsx on the closing of tb24.xlsx" == r["source"]
        for r in monthly
        if r["period"] < "2025-04"
    )
    assert all(r["expected"] == "" and r["checked"] == "" for r in rows)
