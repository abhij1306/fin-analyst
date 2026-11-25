"""FDD F1 end to end through the CLI: a contract reading with terms, coverage, a sample, a
walkthrough with an exception that becomes a query line, and no real name in any output."""

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.register_fixtures import write_registers
from tests.tally_fixtures import CUSTOMERS, VENDORS

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY25", "--threshold", "100000")
    assert opened.exit_code == 0, opened.output
    path = write_registers(tmp_path / "reg.xlsx")
    loaded = fin("ingest", "acme", str(path), "--kind", "registers")
    assert loaded.exit_code == 0, loaded.output
    return data_root


def test_contracts_sample_walkthrough_and_the_query_line(root: Path, tmp_path: Path) -> None:
    contract = tmp_path / "contract.pdf"
    contract.write_bytes(b"synthetic")
    reading = tmp_path / "reading.csv"
    with reading.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["party_kind", "counterparty", "start", "end", "page", "auto_renew", "lock_in_months"]
        )
        writer.writerow(["customer", CUSTOMERS[0], "2024-04-01", "2025-12-31", "2", "yes", "6"])
        writer.writerow(["vendor", VENDORS[0], "2024-04-01", "2026-03-31", "5", "no", ""])
    results = tmp_path / "walk.json"
    results.write_text(
        json.dumps(
            [
                {
                    "line_no": 1, "check": "contract_covers", "result": "exception",
                    "evidence": f"contract rate differs for {CUSTOMERS[0]}",
                    "file": "contract.pdf", "page": 2,
                }
            ]
        ),
        encoding="utf-8",
    )  # fmt: skip

    loaded = fin("recast", "contracts", "acme", str(contract), "--reading", str(reading))
    coverage = fin("contracts", "coverage", "acme")
    drawn = fin(
        "sample", "draw", "acme", "--kind", "sales", "--period", "FY25", "--n", "3", "--seed", "4"
    )
    recorded = fin("walkthrough", "record", "acme", "1", str(results))
    summary = fin("walkthrough", "summary", "acme", "--json")
    bad = fin("sample", "draw", "acme", "--kind", "sales", "--period", "FY30")

    assert loaded.exit_code == 0, loaded.output
    assert "customer CUST-" in loaded.stdout and "vendor VEND-" in loaded.stdout
    assert coverage.exit_code == 0, coverage.output
    assert "auto-renew yes" in coverage.stdout and "no contract on file" in coverage.stdout
    assert drawn.exit_code == 0 and "seed 4" in drawn.stdout, drawn.output
    assert recorded.exit_code == 0, recorded.output
    assert "1 exceptions on 1 lines" in recorded.stdout
    assert "needs a query" in recorded.stdout  # INV-1003, Rs 160000, is above the threshold
    assert json.loads(summary.stdout)[0]["exceptions"] == 1
    assert bad.exit_code == 2 and "E901" in bad.output
    printed = " ".join(r.output for r in (loaded, coverage, drawn, recorded, summary))
    for name in (*CUSTOMERS, *VENDORS):
        assert name not in printed
