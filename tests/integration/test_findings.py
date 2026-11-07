"""Findings instead of failures (fin.findings): a document whose checks fail is held, not rejected;
`fin findings accept` loads it as filed; a fix clears its findings; `fin status` shows it all."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import company_db_path
from fin.status import gaps

runner = CliRunner()
UNBALANCED = "Kestrel_UNBALANCED_balance_sheet.xlsx"


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


def query(root: Path, company: str, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def test_unbalanced_document_is_held_then_accepted_as_filed(
    root: Path, samples: Path, audit_query: Any
) -> None:
    result = fin("ingest", "kestrel", str(samples / "broken" / UNBALANCED))

    assert result.exit_code == 0, result.output
    assert f"kestrel/{UNBALANCED}: held as doc_id 1 version 1: " in result.stdout
    assert query(root, "kestrel", "SELECT status FROM documents") == [("held",)]
    # Held: the facts are kept, but nothing is in current figures or metrics.
    assert query(root, "kestrel", "SELECT count(*) > 0 FROM facts") == [(True,)]
    assert query(root, "kestrel", "SELECT count(*) FROM current_facts") == [(0,)]
    assert query(root, "kestrel", "SELECT count(*) FROM metrics") == [(0,)]

    listed = fin("findings", "list", "kestrel", "--json")
    assert listed.exit_code == 0, listed.output
    [finding] = json.loads(listed.stdout)
    assert {
        key: finding[key]
        for key in (
            "finding_id",
            "status",
            "code",
            "document_status",
            "period",
            "label",
            "fin_value",
            "other_value",
            "fin_source",
            "other_source",
        )
    } == {
        "finding_id": 1,
        "status": "open",
        "code": "E401",
        "document_status": "held",
        "period": "FY25",
        "label": "total_assets vs total_equity_and_liabilities",
        "fin_value": "1291361000.00",
        "other_value": "1286361000.00",
        "fin_source": "Balance Sheet!C31",
        "other_source": "Balance Sheet!C20",
    }

    accepted = fin("findings", "accept", "kestrel", "1")

    assert accepted.exit_code == 0, accepted.output
    assert f"kestrel/{UNBALANCED} (doc_id 1): loaded, 0 open findings left" in accepted.stdout
    assert query(root, "kestrel", "SELECT status FROM documents") == [("loaded",)]
    assert query(root, "kestrel", "SELECT count(*) > 0 FROM metrics WHERE period = 'FY25'") == [
        (True,)
    ]
    # Accepted findings stay attached and listed.
    assert [f["status"] for f in json.loads(fin("findings", "list", "--json").stdout)] == [
        "accepted"
    ]
    assert audit_query("SELECT finding_id, code, status FROM findings ORDER BY recorded_at") == [
        (1, "E401", "open"),
        (1, "E401", "accepted"),
    ]


def test_accepting_a_finding_that_is_not_open_is_e406(root: Path, samples: Path) -> None:
    fin("ingest", "kestrel", str(samples / "broken" / UNBALANCED))
    assert fin("findings", "accept", "kestrel", "1").exit_code == 0

    again = fin("findings", "accept", "kestrel", "1", "7")

    assert again.exit_code == 2
    assert again.stderr.startswith("E406: finding 1 of kestrel is not open (it is accepted)")


def test_a_fix_resolves_the_finding_and_loads_the_document(root: Path, samples: Path) -> None:
    fin("ingest", "kestrel", str(samples / "broken" / UNBALANCED))

    # The fixture adds 50 lakh to FY25 trade receivables; take it out of each line it reached.
    fixed = fin(
        "sql",
        "kestrel",
        "--write",
        "--sql",
        "UPDATE facts SET value = value - 5000000 WHERE doc_id = 1 AND period = 'FY25' "
        "AND account IN ('trade_receivables', 'total_current_assets', 'total_assets')",
    )

    assert fixed.exit_code == 0, fixed.output
    assert json.loads(fixed.stdout)["held_documents_rechecked"] == [
        {"doc_id": 1, "file": UNBALANCED, "open": 0, "status": "loaded"}
    ]
    assert query(root, "kestrel", "SELECT status FROM documents") == [("loaded",)]
    assert query(root, "kestrel", "SELECT code, status FROM findings") == [("E401", "resolved")]
    assert fin("findings", "list", "kestrel").stdout == "no findings\n"


def test_figure_differing_between_documents_is_a_finding_with_both_sides(
    root: Path, samples: Path
) -> None:
    first = samples / "clean" / "bluepeak" / "Bluepeak_Polymers_FY2023-24.xlsx"
    second = samples / "broken" / "Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx"
    assert fin("ingest", "bluepeak", str(first)).exit_code == 0

    result = fin("ingest", "bluepeak", str(second))

    assert result.exit_code == 0, result.output
    rows = json.loads(fin("findings", "list", "bluepeak", "--json").stdout)
    # Revenue and other expenses differ by 3.5 crore, and with them the totals that include them.
    assert [(f["code"], f["file_name"], f["period"], f["label"]) for f in rows] == [
        ("E404", second.name, "FY24", label)
        for label in ("other_expenses", "revenue", "total_expenses", "total_income")
    ]
    # Both sides, each with its file and cell.
    assert {
        key: rows[1][key]
        for key in ("fin_value", "fin_source", "other_value", "other_file", "other_source")
    } == {
        "fin_value": "2478800000.00",
        "fin_source": "Standalone P&L!C7",
        "other_value": "2443800000.00",
        "other_file": first.name,
        "other_source": "Standalone P&L!B7",
    }

    accepted = fin("findings", "accept", "bluepeak", "1", "2", "3", "4")
    assert accepted.exit_code == 0, accepted.output
    assert query(root, "bluepeak", "SELECT file_name, status FROM documents ORDER BY doc_id") == [
        (first.name, "loaded"),
        (second.name, "loaded"),
    ]


def test_status_shows_periods_held_findings_pending_unverified_and_inbox(
    root: Path, samples: Path, tmp_path: Path
) -> None:
    clean = samples / "clean" / "bluepeak"
    assert fin("ingest", "bluepeak", str(clean / "Bluepeak_Polymers_FY2024-25.xlsx")).exit_code == 0
    fin("ingest", "kestrel", str(samples / "broken" / UNBALANCED))
    inbox = root / "inbox" / "bluepeak"
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy(clean / "Bluepeak_Polymers_FY2024-25.xlsx", inbox)  # already ingested
    shutil.copy(clean / "Bluepeak_Polymers_FY2022-23.xlsx", inbox)  # not yet
    fin(
        "sql",
        "bluepeak",
        "--write",
        "--sql",
        "UPDATE facts SET read_by = 'claude' WHERE fact_id <= 2",
    )
    pending = root / "review" / "kestrel" / "pending.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        json.dumps({"company": "kestrel", "next_id": 3, "items": [
            {"id": 1, "status": "pending"}, {"id": 2, "status": "rejected"}
        ]}),
        encoding="utf-8",
    )  # fmt: skip

    result = fin("status", "--json")

    assert result.exit_code == 0, result.output
    bluepeak, kestrel = json.loads(result.stdout)
    assert (bluepeak["company"], kestrel["company"]) == ("bluepeak", "kestrel")
    assert bluepeak["periods"]["years"] == ["FY24", "FY25"]
    assert bluepeak["unverified_claude_facts"] == 2
    assert bluepeak["inbox_not_ingested"] == ["Bluepeak_Polymers_FY2022-23.xlsx"]
    assert kestrel["periods"]["years"] == []  # held: not in current figures
    assert kestrel["held_documents"] == [{"doc_id": 1, "file": UNBALANCED, "open_findings": 1}]
    assert (kestrel["open_findings"], kestrel["accepted_findings"]) == (1, 0)
    assert kestrel["pending_mappings"] == 1  # the rejected item is not counted
    text = fin("status", "kestrel").stdout
    assert f"  held documents: doc_id 1 {UNBALANCED} (1 open)\n" in text
    assert "  findings: 1 open, 0 accepted\n" in text


def test_gaps_are_the_periods_missing_between_the_first_and_last_of_each_kind() -> None:
    periods = ["FY22", "FY25", "FY26-H2", "FY25-Q4", "FY26-Q3", "FY26-9M"]
    assert gaps(periods) == ["FY23", "FY24", "FY26-Q1", "FY26-Q2"]
