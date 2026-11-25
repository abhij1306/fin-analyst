"""`fin provisions` and the F3 cut-off reconciliations end to end through the CLI (docs/fdd.md F3):
Claude proposes provision ledgers, only the user's confirm applies them, the show command labels the
gratuity as indicative, cut-off lines come out of `fin reconcile`, and `fin sql --write` cannot
change the provision ledgers."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.fdd_bsheet_fixtures import seed

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY24,FY25")
    assert opened.exit_code == 0, opened.output
    with connect(company_db_path(data_root, "acme"), write=True) as con:
        seed(con, engagement=False)
    return data_root


def test_provision_ledgers_show_and_cutoff(root: Path, tmp_path: Path, audit_query: Any) -> None:
    proposals = tmp_path / "provisions.json"
    proposals.write_text(
        json.dumps(
            [
                {"ledger": "Provision for Doubtful Debts", "kind": "doubtful_debts", "reason": "a"},
                {"ledger": "Gratuity Payable", "kind": "gratuity", "reason": "by name"},
            ]
        ),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.json"
    bad.write_text('[{"ledger": "Nope", "kind": "gratuity", "reason": "x"}]', encoding="utf-8")

    refused = fin("provisions", "propose", "acme", str(bad))
    proposed = fin("provisions", "propose", "acme", str(proposals))
    before = json.loads(fin("provisions", "show", "acme", "--json").stdout)
    unknown = fin("provisions", "confirm", "acme", "99")
    confirmed = fin("provisions", "confirm", "acme", "1", "2")
    shown = json.loads(fin("provisions", "show", "acme", "--period", "FY25", "--json").stdout)
    text = fin("provisions", "show", "acme")
    cut = fin("reconcile", "acme", "sales_cutoff", "--period", "FY24")
    write = fin("sql", "acme", "--write", "--sql", "DELETE FROM provision_ledgers")

    assert refused.exit_code == 2
    assert "E920" in refused.output
    assert proposed.exit_code == 0, proposed.output
    assert "2 provision ledgers proposed for acme (#1, #2)" in proposed.stdout
    assert {g["provision_inr"] for g in before["gratuity"]} == {None}
    assert unknown.exit_code == 2
    assert "E921" in unknown.output
    assert confirmed.exit_code == 0, confirmed.output
    assert "[confirmed] 'Gratuity Payable' -> gratuity" in confirmed.stdout
    total = [r for r in shown["receivables"] if r["bucket"] == "total"]
    assert [(r["period"], r["open_inr"], r["provision_inr"]) for r in total] == [
        ("FY25", "630000.00", "126000.00")
    ]
    [gratuity] = shown["gratuity"]
    assert (gratuity["gratuity_basic_share"], gratuity["provision_inr"]) == (
        "2288461.53",
        "500000.00",
    )
    assert text.exit_code == 0, text.output
    assert "indicative, undiscounted, not an actuarial valuation" in text.stdout
    assert cut.exit_code == 0, cut.output
    assert "INV-2" in cut.stdout
    assert "billed late" in cut.stdout
    assert write.exit_code != 0
    assert "E806" in write.output
    logged = " ".join(str(row) for row in audit_query("SELECT message FROM events"))
    assert "provision ledger #2: confirmed 'Gratuity Payable' as gratuity" in logged
