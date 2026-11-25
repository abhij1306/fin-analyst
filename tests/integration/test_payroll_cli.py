"""`fin ingest --kind payroll` and `fin payroll confirm-unit` end to end through the CLI: employee
names and codes never reach fin's output or the audit log; the unit needs exactly one answer;
the answer is logged and gate G1 shows it."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.payroll_fixtures import EMPLOYEE_CODES, EMPLOYEE_NAMES, write_payroll

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY25")
    assert opened.exit_code == 0, opened.output
    return data_root


def test_payroll_load_warning_confirmation_and_gate(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    path = write_payroll(tmp_path / "payroll.xlsx", monthly=True)

    loaded = fin("ingest", "acme", str(path), "--kind", "payroll")
    neither = fin("payroll", "confirm-unit", "acme", "payroll.xlsx")
    both = fin("payroll", "confirm-unit", "acme", "payroll.xlsx", "--annual", "--monthly")
    unknown = fin("payroll", "confirm-unit", "acme", "nope.xlsx", "--annual")
    said = fin("payroll", "confirm-unit", "acme", "payroll.xlsx", "--annual")
    status = fin("gate", "status", "acme", "--json")

    assert loaded.exit_code == 0, loaded.output
    assert "WARNING: the amounts look monthly" in loaded.stdout
    assert neither.exit_code == 2
    assert both.exit_code == 2
    assert unknown.exit_code == 2
    assert "E820" in unknown.output
    assert said.exit_code == 0, said.output
    assert said.stdout.startswith("payroll file_id 1 (payroll.xlsx, FY25): the user confirmed")
    texts = [line["text"] for line in json.loads(status.stdout)["gates"][0]["checklist"]]
    assert any(t.startswith("payroll payroll.xlsx (FY25): unit confirmed annual") for t in texts)
    logged = " ".join(str(row) for row in audit_query("SELECT message, context FROM events"))
    assert "the user confirmed the unit is annual" in logged
    printed = loaded.output + said.output + status.output + logged
    for value in (*EMPLOYEE_NAMES, *EMPLOYEE_CODES):
        assert value not in printed
