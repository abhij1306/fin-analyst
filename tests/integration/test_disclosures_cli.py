"""`fin disclosures` end to end (FDD F5): load a reading, list the schedules, propose a related
party link and have the user confirm it in review, then `fin reconcile rpt_vs_books`; gate G2 asks
for it.
A register customer shows as its token; its Tally ledger, by name, is matched to it."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config
from fin.db.schema import ensure_database
from fin.diligence import ledgers
from fin.diligence.sources import ingest_source
from fin.paths import company_db_path
from tests.disclosure_fixtures import ACCOUNTS, LOAN, RAVI, write_set
from tests.tally_fixtures import CUSTOMERS

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> dict[str, Path]:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY24,FY25")
    assert opened.exit_code == 0, opened.output
    files = write_set(tmp_path)
    run = Run("test", {}, data_root)
    for key, kind in (
        ("tb24", "trial_balance"),
        ("tb25", "trial_balance"),
        ("lv", "ledger_vouchers"),
        ("registers", "registers"),
    ):
        ingest_source(run, load_config(data_root), "acme", files[key], kind, None)
    proposals = [
        {"ledger": k, "account": v, "confidence": 0.9, "reason": "by name"}
        for k, v in ACCOUNTS.items()
    ]
    ledgers.propose(run, "acme", proposals)
    ledgers.confirm(run, "acme", [r["ledger_id"] for r in ledgers.list_ledgers(run, "acme")])
    return files


def test_disclosures_load_link_and_reconcile(root: dict[str, Path], tmp_path: Path) -> None:
    loaded = fin(
        "disclosures", "load", "acme", str(root["notes"]), "--reading", str(root["reading"])
    )
    listed = fin("disclosures", "list", "acme", "--kind", "tax_matter")
    parties = fin("disclosures", "related", "acme")
    proposals = tmp_path / "links.json"
    proposals.write_text(
        json.dumps(
            [{"party": RAVI, "kind": "ledger", "target": LOAN, "confidence": 0.9,
              "reason": "the notes name the director's loan"}]
        ),
        encoding="utf-8",
    )  # fmt: skip
    proposed = fin("disclosures", "related", "acme", str(proposals))
    waiting = fin("review", "list", "acme", "--related", "--json")
    link_id = str(json.loads(waiting.stdout)[0]["link_id"])
    confirmed = fin("review", "confirm", "acme", link_id, "--related")
    reconciled = fin("reconcile", "acme", "rpt_vs_books", "--period", "FY25")
    gate = fin("gate", "status", "acme")

    for result in (loaded, listed, parties, proposed, waiting, confirmed, reconciled):
        assert result.exit_code == 0, result.output
    assert "tax_matter (1)" in listed.stdout and "AY 2021-22" in listed.stdout
    assert "CUST-0001: customer CUST-0001 (matched)" in parties.stdout
    assert f"ledger {CUSTOMERS[0]} (matched)" in parties.stdout  # its ledger, through mask_values
    assert f"{RAVI}: not matched" in parties.stdout
    assert f"{LOAN} confirmed" in confirmed.stdout
    assert "rpt_vs_books FY25" in reconciled.stdout
    assert "related_party CUST-0001 sales" in reconciled.stdout
    assert "rpt_vs_books" in gate.output
    assert f"related_party {CUSTOMERS[0]} sales" not in reconciled.stdout  # the token, not the name


def test_a_bad_reading_is_e940(root: dict[str, Path], tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("kind,period,nature\nguarantee,FY25,x\n", encoding="utf-8")

    result = fin("disclosures", "load", "acme", str(root["notes"]), "--reading", str(bad))

    assert result.exit_code == 2
    assert result.stderr.startswith("E940")
