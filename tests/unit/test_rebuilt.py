"""The rebuilt statements (fin.rebuilt, SPEC-14) on a synthetic diligence company: rebuilt equals
reported but for the planted differences, the derived cash flow ties, an unmapped ledger marks the
totals above it incomplete, and a group-only trial balance is refused with what to ask for."""

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import RebuildNotPossible
from fin.db.conn import connect
from fin.paths import company_db_path
from fin.rebuilt.compare import format_rebuilt, rebuilt_rows, select_period
from fin.rebuilt.view import add_views
from tests.rebuilt_fixtures import PLANTED, REPORTED, make_root


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_root(tmp_path_factory.mktemp("rebuilt"))


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def view(root: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        add_views(con, root)
        return con.execute(sql, params or []).fetchall()


def lines(result: dict[str, Any], period: str, statement: str) -> dict[str, dict[str, Any]]:
    return {
        r["account"]: r
        for r in result["rows"]
        if r["period"] == period and r["statement"] == statement and r["account"]
    }


def test_rebuilt_equals_reported_but_for_the_planted_differences(root: Path) -> None:
    result = rebuilt_rows(root, "acme", "FY25")
    by_account = lines(result, "FY25", "balance_sheet") | lines(result, "FY25", "profit_and_loss")
    reported = {account for statement, account, _ in REPORTED if statement != "cash_flow"}
    differences = {a: by_account[a]["difference"] for a in reported}
    assert differences == {a: Decimal(-PLANTED.get(a, 0)) for a in reported}
    assert by_account["revenue"]["rebuilt"] == Decimal("2400000.00")
    assert by_account["revenue"]["sources"] == "tb_FY25.xlsx Trial Balance!23"
    assert by_account["total_assets"]["rebuilt"] == Decimal("1520000.00")
    assert by_account["total_equity_and_liabilities"]["rebuilt"] == Decimal("1520000.00")
    assert by_account["total_equity_and_liabilities"]["reported"] is None
    assert by_account["total_equity_and_liabilities"]["difference"] is None
    assert not any(r["incomplete"] for r in result["rows"])


def test_the_derived_cash_flow_ties_opening_plus_activities_to_closing(root: Path) -> None:
    rows = view(
        root,
        "SELECT period, account, value FROM rebuilt_statements WHERE statement = 'cash_flow' "
        "AND account IN ('cfo', 'cfi', 'cff', 'opening_cash', 'closing_cash')",
    )
    values = {(p, a): v for p, a, v in rows}
    assert values == {
        ("FY25", "cfo"): Decimal("290000.00"),
        ("FY25", "cfi"): Decimal("-220000.00"),
        ("FY25", "cff"): Decimal("0.00"),
        ("FY25", "opening_cash"): Decimal("250000.00"),
        ("FY25", "closing_cash"): Decimal("320000.00"),
    }
    [(gap,)] = view(
        root,
        "SELECT count(*) FROM rebuilt_statements o JOIN rebuilt_statements c USING (period) "
        "JOIN (SELECT period, sum(value) AS v FROM rebuilt_statements "
        "WHERE account IN ('cfo', 'cfi', 'cff') GROUP BY period) a USING (period) "
        "WHERE o.account = 'opening_cash' AND c.account = 'closing_cash' "
        "AND o.value + a.v <> c.value",
    )
    assert gap == 0
    assert view(root, "SELECT count(*) FROM rebuilt_statements WHERE line_type = 'other'") == [(0,)]


def test_the_cash_flow_lines_and_what_is_not_derivable(root: Path) -> None:
    rows = view(
        root,
        "SELECT coalesce(account, label), value, line_type, note FROM rebuilt_statements "
        "WHERE statement = 'cash_flow' ORDER BY line_no",
    )
    values = {key: value for key, value, _, _ in rows}
    assert values["cf_pat"] == Decimal("220000.00")
    assert values["cf_add_depreciation"] == Decimal("120000.00")
    assert values["cf_change_receivables"] == Decimal("-100000.00")
    assert values["cf_change_payables"] == Decimal("30000.00")
    assert values["cf_change_ocl"] == Decimal("20000.00")
    assert values["cf_capex"] == Decimal("-220000.00")
    assert values["cf_lt_borrowings_net"] == Decimal("-100000.00")
    assert values["cf_share_issue_proceeds"] == Decimal("100000.00")
    assert values["Other changes in equity (dividends and other movements)"] == Decimal("0.00")
    derivable = {key: (kind, note) for key, _, kind, note in rows if kind == "not_derivable"}
    assert set(derivable) == {
        "cf_taxes_paid",
        "cf_finance_cost_paid",
        "cf_interest_received",
        "cf_dividends_received",
        "cf_ppe_sale_proceeds",
        "cf_investments_sold",
        "cf_dividends_paid",
        "cf_fx_effect",
    }
    assert values["cf_taxes_paid"] is None
    assert derivable["cf_taxes_paid"][1].startswith("not derivable: ")


def test_the_first_year_has_no_cash_flow_and_says_why(root: Path) -> None:
    result = rebuilt_rows(root, "acme")
    assert {r["period"] for r in result["rows"] if r["statement"] == "cash_flow"} == {"FY25"}
    text = format_rebuilt("acme", select_period(result, "FY24"))
    assert "note: FY24: no cash flow: the trial balance of the year before is not loaded" in text


def test_removing_one_mapping_marks_the_totals_above_it_incomplete(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE ledger_accounts SET status = 'proposed' WHERE ledger = 'Orchid Systems Pvt Ltd'"
        )
    result = rebuilt_rows(root, "acme", "FY25")
    bs = lines(result, "FY25", "balance_sheet")
    assert bs["trade_receivables"]["rebuilt"] is None
    assert bs["trade_receivables"]["incomplete"] is True
    assert bs["trade_receivables"]["unmapped"] == Decimal("500000.00")
    for total in ("total_current_assets", "total_assets"):
        assert bs[total]["incomplete"] is True
        assert bs[total]["unmapped"] == Decimal("500000.00")
        assert bs[total]["unmapped_ledgers"] == 1
    assert bs["total_assets"]["rebuilt"] == Decimal("1020000.00")
    assert bs["total_equity"]["incomplete"] is False
    assert not any(r["incomplete"] for r in lines(result, "FY25", "profit_and_loss").values())
    [memo] = [
        r
        for r in result["rows"]
        if r["line_type"] == "unmapped" and r["statement"] == "balance_sheet"
    ]
    assert memo["rebuilt"] == Decimal("500000.00")
    assert memo["unmapped_ledgers"] == 1
    text = format_rebuilt("acme", result)
    assert "incomplete: 1 unmapped ledgers under it, 0.5 INR Mn (debit - credit)" in text
    cash_flow = [r for r in result["rows"] if r["statement"] == "cash_flow"]
    assert cash_flow
    assert all(r["incomplete"] for r in cash_flow)
    assert "unmapped" not in {r["line_type"] for r in cash_flow}


def test_a_year_with_no_confirmed_ledger_has_no_cash_flow_totals(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE ledger_accounts SET status = 'proposed'")
    rows = view(
        root,
        "SELECT account, value FROM rebuilt_statements WHERE statement = 'cash_flow' "
        "AND account IN ('cfo', 'cfi', 'cff', 'net_change_cash', 'opening_cash', 'closing_cash')",
    )
    assert all(value is None for _, value in rows)
    assert {account for account, _ in rows} <= {"opening_cash", "closing_cash"}


def test_a_ledger_with_no_proposal_marks_every_total_of_its_year(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE ledger_accounts SET status = 'pending', account = NULL WHERE ledger = 'Rent'"
        )
    result = rebuilt_rows(root, "acme", "FY24")
    shown = [r for r in result["rows"] if r["line_type"] == "line"]
    assert shown
    assert all(r["incomplete"] for r in shown)
    assert {r["unmapped"] for r in shown} == {Decimal("350000.00")}


def test_a_newer_trial_balance_replaces_the_old_one(root: Path, tmp_path: Path) -> None:
    from fin.config import load_config
    from fin.diligence.sources import ingest_source
    from tests.rebuilt_fixtures import PERIODS, TRIAL_BALANCES, run
    from tests.recon_fixtures import write_trial_balance

    groups = {k: list(v) for k, v in TRIAL_BALANCES["FY25"].items()}
    groups["Sales Accounts"] = [("Software Sales", 0, 2450000)]
    groups["Bank Accounts"] = [("Axis Bank Current Account", 370000, 0)]
    path = write_trial_balance(tmp_path / "tb_FY25_v2.xlsx", groups, PERIODS["FY25"])
    ingest_source(run(root), load_config(root), "acme", path, "trial_balance", None)
    rows = view(
        root,
        "SELECT value FROM rebuilt_statements WHERE period = 'FY25' AND account = 'revenue'",
    )
    assert rows == [(Decimal("2450000.00"),)]


def test_a_group_only_trial_balance_is_refused_with_what_to_ask_for(tmp_path: Path) -> None:
    root = make_root(tmp_path, periods=("FY25",))
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO source_files (engagement_id, kind, file_name, sha256, archive_path, "
            "period, period_start, period_end, basis, profile, status, run_id, ingested_at) "
            "SELECT engagement_id, 'trial_balance', 'condensed.xlsx', 'c', 'x', 'FY24', "
            "DATE '2023-04-01', DATE '2024-03-31', 'audited', 'tally', 'loaded', 'r0', now() "
            "FROM engagement"
        )
        con.execute(
            "INSERT INTO trial_balance SELECT file_id, 'TB', 6, 'FY24', 'group', "
            "'Capital Account', NULL, NULL, 0, 500000, 'r0' FROM source_files "
            "WHERE file_name = 'condensed.xlsx'"
        )
    with pytest.raises(RebuildNotPossible) as refused:
        rebuilt_rows(root, "acme", "FY24")
    assert refused.value.code == "E822"
    assert refused.value.message == (
        "FY24: condensed.xlsx has group totals only, no ledgers; ask the company for the FY24 "
        "trial balance from Tally in Detailed mode, every ledger with its closing balance"
    )
    result = rebuilt_rows(root, "acme")
    assert {r["period"] for r in result["rows"]} == {"FY25"}


def test_nothing_to_rebuild_is_e822_naming_each_year(tmp_path: Path) -> None:
    root = make_root(tmp_path, periods=())
    with pytest.raises(RebuildNotPossible) as refused:
        rebuilt_rows(root, "acme")
    assert refused.value.message.startswith(
        "acme: no year has a ledger-level trial balance to rebuild from: FY24: no trial balance "
        "loaded; ask the company for the FY24 trial balance"
    )
    with pytest.raises(RebuildNotPossible) as unknown:
        rebuilt_rows(root, "acme", "FY23")
    assert unknown.value.message == "FY23 is not a period of acme's engagement (FY24, FY25)"


def test_fin_statements_rebuilt_prints_the_three_columns(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from fin.cli import app

    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    result = CliRunner().invoke(app, ["statements", "acme", "--rebuilt"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == (
        "acme standalone FY25: profit and loss, reported and rebuilt from the trial balance "
        "(INR Mn)"
    )
    [revenue] = [line for line in lines if line.startswith("  Revenue from operations")]
    assert revenue.split()[-3:] == ["2.5", "2.4", "(0.1)"]
    refused = CliRunner().invoke(
        app, ["statements", "acme", "--rebuilt", "--scope", "consolidated"]
    )
    assert refused.exit_code == 2
    assert "standalone" in refused.output


def test_the_export_adds_the_rebuilt_sheets(root: Path) -> None:
    from openpyxl import load_workbook

    from fin.workbook import export_workbook

    book = load_workbook(export_workbook(root, "acme"))
    assert book.sheetnames[-3:] == ["Rebuilt P&L", "Rebuilt Balance sheet", "Rebuilt Cash flow"]
    sheet = book["Rebuilt P&L"]
    assert sheet["A1"].value == "acme: Rebuilt P&L, standalone (INR Mn)"
    header = [c.value for c in sheet[2]]
    assert header[:4] == ["Line", "FY24 reported", "FY24 rebuilt", "FY24 difference"]
    rows = {row[0].value: [c.value for c in row] for row in sheet.iter_rows(min_row=3)}
    revenue = rows["Revenue from operations"]
    assert revenue[4:7] == [2.5, 2.4, -0.1]
    assert revenue[1:4] == [None, 2, None]
    assert sheet.cell(3, 5).number_format == '#,##0.0;(#,##0.0);"–"'
    cash = {row[0].value: row[-1].value for row in book["Rebuilt Cash flow"].iter_rows(min_row=3)}
    assert cash["Income taxes paid"].startswith("FY25: not derivable")
