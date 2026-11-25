"""`fin netdebt` and `fin review ... --netdebt` end to end through the CLI (docs/fdd.md F4): Claude
proposes net debt classes and debt-like items, only the user's confirm or accept applies them, the
show command labels the stub and the proposed items, and `fin sql --write` cannot change them."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.fdd_netdebt_fixtures import seed

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY24,FY25", "--stub", "FY26-YTD-SEP")
    assert opened.exit_code == 0, opened.output
    with connect(company_db_path(data_root, "acme"), write=True) as con:
        seed(con, engagement=False)
    return data_root


def test_classes_items_and_show(root: Path, tmp_path: Path, audit_query: Any) -> None:
    proposals = tmp_path / "classes.json"
    proposals.write_text(
        json.dumps(
            [
                {"ledger": "Gratuity Provision", "class": "debt_like", "reason": "benefit"},
                {"ledger": "CUST-0001", "class": "none", "reason": "trade receivable"},
                {"ledger": "TDS Payable", "class": "debt_like", "reason": "statutory due"},
            ]
        ),
        encoding="utf-8",
    )
    bad = tmp_path / "bad.json"
    bad.write_text('[{"ledger": "Nope", "class": "debt", "reason": "x"}]', encoding="utf-8")

    waiting = fin("netdebt", "propose", "acme")
    refused = fin("netdebt", "propose", "acme", str(bad))
    proposed = fin("netdebt", "propose", "acme", str(proposals))
    listed = fin("review", "list", "acme", "--netdebt")
    confirmed = fin("review", "confirm", "acme", "1", "2", "--netdebt")
    mixed = fin("review", "confirm", "acme", "3", "--netdebt", "--account", "cash")
    two_modes = fin("review", "reject", "acme", "3", "--netdebt", "--ledgers")
    rejected = fin("review", "reject", "acme", "3", "--netdebt")
    item = fin(
        "netdebt", "item", "propose", "acme", "--period", "FY25", "--amount", "200000",
        "--description", "Gratuity shortfall", "--evidence", "fin provisions show FY25",
    )  # fmt: skip
    other = fin(
        "netdebt", "item", "propose", "acme", "--period", "FY25", "--amount", "75000",
        "--description", "Contingency", "--evidence", "notes p. 12",
    )  # fmt: skip
    bad_period = fin(
        "netdebt", "item", "propose", "acme", "--period", "FY23", "--amount", "1",
        "--description", "x", "--evidence", "y",
    )  # fmt: skip
    accepted = fin("netdebt", "item", "accept", "acme", "1")
    no_reason = fin("netdebt", "item", "reject", "acme", "2", "--reason", " ")
    items = fin("netdebt", "item", "list", "acme", "--json")
    shown = json.loads(fin("netdebt", "show", "acme", "--json").stdout)
    text = fin("netdebt", "show", "acme")
    write = fin("sql", "acme", "--write", "--sql", "DELETE FROM netdebt_items")

    assert waiting.exit_code == 0, waiting.output
    assert "'Suspense'" in waiting.stdout
    assert "'Term Loan'" not in waiting.stdout
    assert refused.exit_code == 2
    assert "E930" in refused.output
    assert proposed.exit_code == 0, proposed.output
    assert "3 net debt classes proposed for acme (#1, #2, #3)" in proposed.stdout
    assert listed.stdout.count("[proposed]") == 3
    assert confirmed.exit_code == 0, confirmed.output
    assert "[confirmed] 'Gratuity Provision' -> debt_like" in confirmed.stdout
    assert mixed.exit_code == 2
    assert two_modes.exit_code == 2
    assert rejected.exit_code == 0, rejected.output
    assert "[rejected] 'TDS Payable'" in rejected.stdout
    assert item.exit_code == 0, item.output
    assert "net debt item #1 [proposed] FY25" in item.stdout
    assert other.exit_code == 0, other.output
    assert bad_period.exit_code == 2
    assert "E932" in bad_period.output
    assert accepted.exit_code == 0, accepted.output
    assert "[accepted]" in accepted.stdout
    assert no_reason.exit_code == 2
    assert "E933" in no_reason.output
    assert [i["status"] for i in json.loads(items.stdout)] == ["accepted", "proposed"]
    fy25 = next(r for r in shown["net_debt"] if r["period"] == "FY25")
    assert (fy25["cash"], fy25["debt"], fy25["debt_like"], fy25["net_debt"]) == (
        "-10000.00",
        "850000.00",
        "320000.00",
        "1180000.00",
    )
    assert (fy25["proposed_items"], fy25["unclassified_ledgers"]) == ("75000.00", 2)
    assert text.exit_code == 0, text.output
    assert "FY26-YTD-SEP (unaudited management accounts)" in text.stdout
    assert "proposed items, not applied: 1" in text.stdout
    assert write.exit_code != 0
    assert "E806" in write.output
    logged = " ".join(str(row) for row in audit_query("SELECT message FROM events"))
    assert "net debt class #1: confirmed 'Gratuity Provision' as debt_like" in logged
    assert "net debt item #1: accepted" in logged
