"""`fin fdd finding` and `fin report --fdd` end to end through the CLI (docs/fdd.md F8): Claude
proposes findings with an amount only from linked evidence, only the user's final reaches the
report, `fin sql --write` cannot change them, and the first report run writes the FDD stub."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import company_db_path

runner = CliRunner()

PROPOSE = [
    "fdd", "finding", "propose", "acme", "--area", "qoe", "--title", "Legal fees added back",
    "--observation", "Legal fees for a settled dispute are in other expenses.",
    "--severity", "high", "--impact", "ebitda", "--deal", "spa_protection",
    "--evidence", "Notes to accounts p. 12", "--adjustment", "1",
]  # fmt: skip


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY24,FY25")
    assert opened.exit_code == 0, opened.output
    with connect(company_db_path(data_root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO adjustments (engagement_id, period, scope, category, amount, "
            "revenue_effect, cost_effect, description, source_ref, status, created_run_id, "
            "created_at, status_run_id, status_at) VALUES (1, 'FY25', 'standalone', "
            "'normalisation', 250000, 0, -250000, 'One-off legal fees', 'Notes p. 12', "
            "'proposed', 'r', now(), 'r', now())"
        )
    return data_root


def test_findings_through_the_cli(root: Path, audit_query: Any) -> None:
    proposed = fin(*PROPOSE)
    typed = fin(*PROPOSE[:-2], "--observation", "Fees of 25 lakh.")
    listed = fin("fdd", "finding", "list", "acme", "--json")
    final = fin("fdd", "finding", "final", "acme", "1")
    again = fin("fdd", "finding", "drop", "acme", "1")
    write = fin("sql", "acme", "--write", "--sql", "UPDATE fdd_findings SET severity = 'low'")

    assert proposed.exit_code == 0, proposed.output
    assert "[draft] high qoe: Legal fees added back" in proposed.output
    assert "from adjustment #1 (proposed)" in proposed.output
    assert typed.exit_code == 2 and "E970" in typed.output
    rows = json.loads(listed.output)
    assert [(r["finding_id"], r["status"]) for r in rows] == [(1, "draft")]
    assert final.exit_code == 0 and "[final]" in final.output
    assert again.exit_code == 2 and "E971" in again.output
    assert write.exit_code != 0 and "E806" in write.output
    events = audit_query("SELECT message FROM events WHERE stage = 'fdd' ORDER BY recorded_at, seq")
    assert [e[0] for e in events] == [
        "FDD finding #1: proposed (high, qoe)",
        "FDD finding #1: draft -> final",
    ]


def test_the_fdd_report_writes_its_stub_first(root: Path) -> None:
    result = fin("report", "acme", "--fdd")

    assert result.exit_code == 0, result.output
    path = Path(json.loads(result.output)["narrative_path"])
    assert path.name == "fdd_narrative.md"
    assert "## Key findings" in path.read_text(encoding="utf-8")
    both = fin("report", "acme", "--fdd", "--diligence")
    assert both.exit_code != 0
