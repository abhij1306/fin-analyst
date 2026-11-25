"""Normalised working capital (db/schema/diligence_nwc.sql) on the synthetic engagement of
tests/qoe_fixtures.py: year-end and stub figures from the trial balances, monthly figures from the
Ledger Vouchers on the previous trial balance's closing, only for ledgers that tie."""

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.db.conn import connect
from fin.paths import company_db_path
from tests.qoe_fixtures import build_root

D = Decimal


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_root(tmp_path_factory.mktemp("qoe"))


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def test_year_end_working_capital_is_the_trial_balance_rolled_up_by_component(root: Path) -> None:
    got = rows(
        root,
        "SELECT period, basis, trade_receivables, trade_payables, net_working_capital, "
        "unmapped_ledgers FROM nwc_year_end ORDER BY period_end",
    )
    assert got == [
        ("FY24", "audited", D("150000"), D("70000"), D("80000"), 0),
        ("FY25", "audited", D("764000"), D("200000"), D("564000"), 0),
        ("FY26-YTD-SEP", "unaudited", D("900000"), D("250000"), D("650000"), 0),
    ]


def test_every_year_end_figure_has_its_trial_balance_rows(root: Path) -> None:
    got = rows(
        root,
        'SELECT period, ledger, balance, sheet, "row" FROM nwc_year_end_lines '
        "WHERE period = 'FY24' ORDER BY ledger",
    )
    assert [(p, ledger, b) for p, ledger, b, _, _ in got] == [
        ("FY24", "CUST-0001", D("100000")),
        ("FY24", "CUST-0002", D("50000")),
        ("FY24", "VEND-0001", D("50000")),
        ("FY24", "VEND-0002", D("20000")),
    ]
    assert all(sheet == "Trial Balance" and row > 0 for _, _, _, sheet, row in got)


def test_monthly_working_capital_starts_from_the_previous_year_end(root: Path) -> None:
    got = rows(
        root,
        "SELECT month_end::VARCHAR, net_working_capital, ledgers_included, ledgers_excluded "
        "FROM nwc_monthly WHERE period = 'FY25' ORDER BY month_end",
    )
    assert [(m, n) for m, n, _, _ in got[:3]] == [
        ("2024-04-30", D("80000")),
        ("2024-05-31", D("694000")),
        ("2024-06-30", D("564000")),
    ]
    assert len(got) == 12 and got[-1][0] == "2025-03-31"
    assert {(i, x) for _, _, i, x in got} == {(4, 0)}
    # The last month is the year-end trial balance when every ledger is in.
    assert got[-1][1] == D("564000")


def test_a_ledger_that_does_not_tie_is_left_out_with_its_reason(root: Path) -> None:
    got = rows(
        root,
        "SELECT ledger, status, reason FROM nwc_monthly_ledgers WHERE period = 'FY26-YTD-SEP' "
        "ORDER BY ledger",
    )
    assert ("CUST-0002", "excluded", "movements do not tie to the trial balance") in got
    month = rows(
        root,
        "SELECT month_end::VARCHAR, ledgers_included, ledgers_excluded FROM nwc_monthly "
        "WHERE period = 'FY26-YTD-SEP' ORDER BY month_end",
    )
    assert month and month[-1][0] == "2025-09-30" and month[-1][2] > 0


def test_a_reconciliation_that_ran_on_other_files_is_not_used(root: Path, tmp_path: Path) -> None:
    copy = Path(shutil.copytree(root, tmp_path / "data"))
    with connect(company_db_path(copy, "acme"), write=True) as con:
        con.execute("UPDATE recon_runs SET right_file_id = 999 WHERE period = 'FY25'")
    assert rows(copy, "SELECT count(*) FROM nwc_monthly WHERE period = 'FY25'") == [(0,)]
    assert rows(copy, "SELECT DISTINCT reason FROM nwc_monthly_ledgers WHERE period = 'FY25'") == [
        ("gl_vs_tb has not run on the current files",)
    ]


def test_the_first_year_has_no_monthly_view_without_the_previous_trial_balance(
    root: Path,
) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE source_files SET status = 'superseded' "
            "WHERE period = 'FY24' AND kind = 'trial_balance'"
        )
    got = rows(root, "SELECT count(*) FROM nwc_monthly WHERE period = 'FY25'")
    assert got == [(0,)]
    reasons = rows(
        root,
        "SELECT DISTINCT reason FROM nwc_monthly_ledgers WHERE period = 'FY25'",
    )
    assert reasons == [("no previous trial balance",)]
