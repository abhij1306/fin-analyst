"""`fin ingest` on an annual-report PDF holding standalone and consolidated statements."""

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
UNIT = (50, 65, "(Rs. in Lakhs)")
HEADER = [(380, 85, "31 March 2025"), (470, 85, "31 March 2024")]


def statement(title: str, *lines: tuple[str, str, str]) -> Page:
    """A statement page: title, unit, header, then (label, FY25, FY24) rows."""
    page: Page = [(50, 50, title), UNIT, *HEADER]
    for n, (label, fy25, fy24) in enumerate(lines):
        y = 105 + 15 * n
        page += [(50, y, label), (390, y, fy25), (480, y, fy24)]
    return page


def report(path: Path, *cash_flow_extra: tuple[str, str, str]) -> Path:
    return write_pdf(
        path,
        statement(
            "STANDALONE BALANCE SHEET",
            ("Cash and cash equivalents", "28.50", "25.62"),
            ("Security deposits", "71.50", "64.38"),  # unmapped, so no subtotal check runs
            ("TOTAL ASSETS", "100.00", "90.00"),
            ("TOTAL EQUITY AND LIABILITIES", "100.00", "90.00"),
        ),
        statement(
            "STANDALONE STATEMENT OF CASH FLOWS",
            ("Cash and cash equivalents at the end of the year", "18.20", "25.62"),
            *cash_flow_extra,
        ),
        statement(
            "CONSOLIDATED BALANCE SHEET",
            ("TOTAL ASSETS", "120.00", "110.00"),
            ("TOTAL EQUITY AND LIABILITIES", "120.00", "110.00"),
        ),
    )


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def query(root: Path, sql: str) -> list[Any]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def test_scopes_stay_apart_and_a_reconciled_overdraft_passes_e403(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    path = report(
        tmp_path / "acme_ar25.pdf",
        ("Cash and cash equivalents as above", "28.50", "25.62"),
        ("Bank overdraft", "(10.30)", "-"),
    )

    result = runner.invoke(app, ["ingest", "acme", str(path)])

    assert result.exit_code == 0, result.stderr
    assert query(
        root,
        "SELECT scope, account, fiscal_year, value FROM facts WHERE account LIKE 'total%' "
        "AND fiscal_year = 'FY25' ORDER BY ALL",
    ) == [
        ("consolidated", "total_assets", "FY25", Decimal("12000000.00")),
        ("consolidated", "total_equity_and_liabilities", "FY25", Decimal("12000000.00")),
        ("standalone", "total_assets", "FY25", Decimal("10000000.00")),
        ("standalone", "total_equity_and_liabilities", "FY25", Decimal("10000000.00")),
    ]
    assert query(root, "SELECT scope FROM documents") == [("consolidated,standalone",)]
    checks = audit_query(
        "SELECT fiscal_year, scope, passed, message FROM validation_results "
        "WHERE check_name = 'cash_flow' ORDER BY fiscal_year"
    )
    assert [row[:3] for row in checks] == [
        ("FY24", "standalone", True),
        ("FY25", "standalone", True),
    ]
    assert checks[1][3].endswith(
        "reconciled in the cash flow statement by the line 'Cash and cash equivalents as above'"
    )


def test_closing_cash_that_does_not_reconcile_still_fails_e403(root: Path, tmp_path: Path) -> None:
    path = report(tmp_path / "acme_ar25.pdf")

    result = runner.invoke(app, ["ingest", "acme", str(path)])

    assert result.exit_code == 1
    assert "E403: FY25 standalone: cash flow closing cash vs balance-sheet cash" in result.stdout
    assert query(root, "SELECT count(*) FROM facts") == [(0,)]
