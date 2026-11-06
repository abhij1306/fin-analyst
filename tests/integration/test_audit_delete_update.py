"""`fin audit`, `fin delete` and `fin update` (Phase 11) on the synthetic Kestrel workbook."""

import json
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

import fin.update
from fin.cli import app
from fin.db.conn import connect
from fin.paths import analytics_db_path, company_db_path

runner = CliRunner()
SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "sample-financials"
KESTREL_FILE = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
KEY = json.loads((SAMPLES / "answer_key.json").read_text(encoding="utf-8"))


def invoke(*args: str, input: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=input)


@pytest.fixture
def kestrel(data_root: Path) -> Path:
    """A data folder with Kestrel ingested and one question answered."""
    assert invoke("init").exit_code == 0
    result = invoke("ingest", "kestrel", str(SAMPLES / "clean" / "kestrel"))
    assert result.exit_code == 0, result.output
    return data_root


def fact_id(root: Path, account: str, year: str) -> int:
    with connect(company_db_path(root, "kestrel")) as con:
        return con.execute(
            "SELECT fact_id FROM facts WHERE account = ? AND fiscal_year = ? "
            "AND scope = 'standalone'",
            [account, year],
        ).fetchone()[0]


# --- fin audit ---


def test_audit_traces_a_fact_to_its_cell(kestrel: Path) -> None:
    result = invoke(
        "audit", "kestrel", "--fact", str(fact_id(kestrel, "revenue", "FY25")), "--json"
    )
    assert result.exit_code == 0, result.output
    trace = json.loads(result.stdout)

    assert trace["fact"]["account"] == "revenue"
    assert (
        trace["fact"]["value"]
        == str(KEY["companies"]["kestrel"]["facts"]["standalone"]["FY25"]["revenue"]) + ".00"
    )
    assert trace["document"]["file_name"] == KESTREL_FILE
    assert trace["raw_value"]["source"] == "Profit and Loss!C7"
    assert trace["document"]["archive_exists"] is True
    assert trace["mapping"]["dictionary"]["source"] == "seed"
    ingest = trace["ingest_run"]
    assert ingest["command"] == "ingest"
    assert ingest["status"] == "ok"
    assert any("loaded as doc_id" in event["message"] for event in ingest["events"])


def test_audit_fact_text_names_file_and_cell(kestrel: Path) -> None:
    result = invoke("audit", "kestrel", "--fact", str(fact_id(kestrel, "revenue", "FY25")))
    assert result.exit_code == 0
    assert f"source: {KESTREL_FILE} Profit and Loss!C7" in result.stdout


def test_audit_unknown_fact_is_e504(kestrel: Path) -> None:
    result = invoke("audit", "kestrel", "--fact", "999999")
    assert result.exit_code == 2
    assert "E504" in result.output


def test_audit_traces_a_metric_to_its_cells(kestrel: Path) -> None:
    result = invoke("audit", "kestrel", "ebitda", "FY25", "--json")
    assert result.exit_code == 0, result.output
    trace = json.loads(result.stdout)

    (scope,) = trace["scopes"]
    assert scope["scope"] == "standalone"
    assert scope["unit"] == "rupees"
    document = KEY["companies"]["kestrel"]["documents"][0]
    cells = document["locations"]["FY25"]
    facts = KEY["companies"]["kestrel"]["facts"]["standalone"]["FY25"]
    inputs = {item["account"]: item["trace"] for item in scope["inputs"]}
    assert sorted(inputs) == ["depreciation", "finance_cost", "other_income", "pbt"]
    for account, traced in inputs.items():
        assert traced["raw_value"]["source"] == cells[account]
        assert traced["document"]["file_name"] == KESTREL_FILE
        assert traced["fact"]["value"] == f"{facts[account]}.00"
    expected = facts["pbt"] + facts["finance_cost"] + facts["depreciation"] - facts["other_income"]
    assert Decimal(scope["value"]) == expected
    assert cells["pbt"] in invoke("audit", "kestrel", "ebitda", "FY25").stdout


def test_audit_unknown_metric_is_e504(kestrel: Path) -> None:
    result = invoke("audit", "kestrel", "no_such_metric", "FY25")
    assert result.exit_code == 2
    assert "E504" in result.output


def test_audit_history_lists_ingest_and_query(kestrel: Path) -> None:
    sql = "SELECT f.fact_id, f.value FROM facts f WHERE f.account = 'revenue'"
    assert invoke("query", "kestrel", "--question", "all revenue?", "--sql", sql).exit_code == 0
    result = invoke("audit", "kestrel", "--json")
    assert result.exit_code == 0, result.output
    history = json.loads(result.stdout)

    assert [run["command"] for run in history["runs"]] == ["ingest", "query"]
    assert [q["question"] for q in history["queries"]] == ["all revenue?"]
    assert "history of kestrel" in invoke("audit", "kestrel").stdout


# --- fin delete ---


@pytest.fixture
def kestrel_with_files(kestrel: Path) -> Path:
    """Kestrel plus an inbox file, a query with an evidence page, and another company's folder."""
    (kestrel / "inbox" / "kestrel").mkdir(parents=True)
    (kestrel / "inbox" / "kestrel" / "new.xlsx").write_text("x")
    (kestrel / "inbox" / "other").mkdir(parents=True)
    sql = "SELECT f.fact_id, f.value FROM facts f WHERE f.account = 'revenue'"
    query = invoke("query", "kestrel", "--question", "revenue?", "--sql", sql)
    query_id = json.loads(query.stdout)["query_id"]
    assert invoke("evidence", query_id).exit_code == 0
    assert (kestrel / "evidence" / f"{query_id}.html").is_file()
    return kestrel


def test_delete_with_wrong_name_changes_nothing(kestrel_with_files: Path) -> None:
    result = invoke("delete", "kestrel", input="kestrl\n")
    assert result.exit_code == 3
    assert "E701" in result.output
    assert company_db_path(kestrel_with_files, "kestrel").is_file()


def test_delete_removes_company_keeps_audit_trail(
    kestrel_with_files: Path, audit_query: Callable[..., list[tuple[Any, ...]]]
) -> None:
    root = kestrel_with_files
    result = invoke("delete", "kestrel", input="kestrel\n")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout[result.stdout.index("{") :])

    # The company's files are gone; another company's folder is not touched.
    assert not company_db_path(root, "kestrel").exists()
    for folder in ("inbox", "archive", "notes"):
        assert not (root / folder / "kestrel").exists()
    assert list((root / "evidence").glob("*.html")) == []
    assert (root / "inbox" / "other").is_dir()

    # The company's rows are gone from the analytics export.
    with connect(analytics_db_path(root)) as con:
        assert con.execute("SELECT count(*) FROM metrics").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM companies").fetchone()[0] == 0

    # The backup holds what was removed.
    backup = root / payload["backup_path"]
    assert (backup / "companies" / "kestrel.duckdb").is_file()
    assert (backup / "inbox" / "kestrel" / "new.xlsx").is_file()
    assert any((backup / "archive" / "kestrel").iterdir())

    # The deletions row records it, and the audit trail still has the company's history.
    ((files_removed, row_counts, backup_path),) = audit_query(
        "SELECT files_removed, row_counts, backup_path FROM deletions WHERE company = 'kestrel'"
    )
    assert "companies/kestrel.duckdb" in json.loads(files_removed)
    counts = json.loads(row_counts)
    assert counts["documents"] == 1
    assert counts["facts"] > 0
    assert backup_path == payload["backup_path"]
    commands = [r[0] for r in audit_query("SELECT command FROM runs WHERE record = 'start'")]
    assert "ingest" in commands
    assert audit_query("SELECT count(*) FROM queries WHERE company = 'kestrel'")[0][0] > 0
    history = json.loads(invoke("audit", "kestrel", "--json").stdout)
    assert len(history["deletions"]) == 1


def test_delete_yes_skips_the_prompt(kestrel: Path) -> None:
    result = invoke("delete", "kestrel", "--yes")
    assert result.exit_code == 0, result.output
    assert not company_db_path(kestrel, "kestrel").exists()


def test_delete_unknown_company_is_e001(kestrel: Path) -> None:
    result = invoke("delete", "nobody", "--yes")
    assert result.exit_code == 2
    assert "E001" in result.output


# --- fin update ---


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """(clone, other): a clone `fin update` runs in, and a second clone used to push new commits to
    the shared bare remote. The uv and npm steps are replaced by no-ops; migrate and doctor run
    through `python -m fin`."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    (other / "a.txt").write_text("one")
    git(other, "add", ".")
    git(other, "commit", "-m", "first")
    git(other, "push", "origin", "HEAD:main")
    work = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(work))
    noop = [sys.executable, "-c", "pass"]
    monkeypatch.setattr(fin.update, "REPO", work)
    monkeypatch.setattr(fin.update, "UV_SYNC", noop)
    monkeypatch.setattr(fin.update, "NPM_CI", noop)
    monkeypatch.setattr(fin.update, "FIN", [sys.executable, "-m", "fin"])
    return work, other


def test_update_pulls_backs_up_and_migrates(
    clone: tuple[Path, Path],
    data_root: Path,
    audit_query: Callable[..., list[tuple[Any, ...]]],
) -> None:
    work, other = clone
    assert invoke("init").exit_code == 0
    # An empty company file is at schema version 0, so `fin migrate` has work to do on it.
    with connect(data_root / "companies" / "old.duckdb", write=True):
        pass
    (other / "b.txt").write_text("two")
    git(other, "add", ".")
    git(other, "commit", "-m", "second")
    git(other, "push", "origin", "HEAD:main")

    result = invoke("update")

    assert result.exit_code in (0, 1), result.output  # 1 only if fin doctor finds a missing tool
    assert (work / "b.txt").read_text() == "two"
    ((db_file, to_version, backup_path, status),) = audit_query(
        "SELECT db_file, to_version, backup_path, status FROM migrations "
        "WHERE db_file = 'companies/old.duckdb'"
    )
    assert (to_version, status) == (3, "applied")
    assert (data_root / backup_path).is_file()
    run_id = audit_query("SELECT run_id FROM runs WHERE command = 'update' AND record = 'start'")[
        0
    ][0]
    for saved in ("shared/audit.duckdb", "shared/dictionary.duckdb", "companies/old.duckdb"):
        assert (data_root / "backups" / run_id / saved).is_file()
    steps = [
        r[0]
        for r in audit_query(
            "SELECT message FROM events WHERE run_id = ? AND stage = 'update' ORDER BY seq",
            [run_id],
        )
    ]
    assert [s.split(":")[0] for s in steps if ": exit" in s] == [
        "git status",
        "git pull",
        "uv sync",
        "fin migrate",
        "fin metrics refresh",
        "fin doctor",
    ]


def test_update_refuses_local_changes(clone: tuple[Path, Path], data_root: Path) -> None:
    work, other = clone
    assert invoke("init").exit_code == 0
    (other / "b.txt").write_text("two")
    git(other, "add", ".")
    git(other, "commit", "-m", "second")
    git(other, "push", "origin", "HEAD:main")
    (work / "scratch.txt").write_text("mine")

    result = invoke("update")

    assert result.exit_code == 2
    assert "E005" in result.output
    assert not (work / "b.txt").exists()
    assert not (data_root / "backups").exists() or not any((data_root / "backups").iterdir())
