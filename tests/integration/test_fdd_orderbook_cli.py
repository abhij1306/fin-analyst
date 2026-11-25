"""FDD F7 end to end through the CLI: load an orderbook, see visibility, run the reconciliation, and
no real name in any output."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.orderbook_fixtures import MAPLE, write_orderbook
from tests.register_fixtures import write_registers
from tests.tally_fixtures import CUSTOMERS

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY25", "--threshold", "100000")
    assert opened.exit_code == 0, opened.output
    loaded = fin(
        "ingest", "acme", str(write_registers(tmp_path / "reg.xlsx")), "--kind", "registers"
    )
    assert loaded.exit_code == 0, loaded.output
    return data_root


def test_orderbook_visibility_and_reconciliation(root: Path, tmp_path: Path) -> None:
    broken = fin(
        "ingest", "acme", str(write_orderbook(tmp_path / "bad.xlsx", bad_value=True)),
        "--kind", "orderbook",
    )  # fmt: skip
    loaded = fin(
        "ingest", "acme", str(write_orderbook(tmp_path / "ob.xlsx")), "--kind", "orderbook"
    )
    shown = fin("visibility", "acme")
    as_json = fin("visibility", "acme", "--json")
    recon = fin("reconcile", "acme", "orderbook_vs_register")
    refused = fin("sql", "acme", "--write", "--sql", "DELETE FROM orderbook_lines")

    assert broken.exit_code == 2 and "E960" in broken.output
    assert loaded.exit_code == 0, loaded.output
    assert "4 orders" in loaded.stdout and "not in INR" in loaded.stdout
    assert shown.exit_code == 0, shown.output
    assert (
        "unbilled orderbook" in shown.stdout
        and "customer contracts remaining: not loaded" in shown.stdout
    )
    data = json.loads(as_json.stdout)
    assert [s["source"] for s in data["sources"]] == ["orderbook", "billed_not_earned", "contract"]
    assert recon.exit_code == 0, recon.output
    assert "needs a query" in recon.stdout  # O-2: Maple has no register billing, 500000
    assert refused.exit_code != 0 and "E806" in refused.output
    printed = " ".join(r.output for r in (loaded, shown, as_json, recon))
    for name in (*CUSTOMERS, MAPLE):
        assert name not in printed
