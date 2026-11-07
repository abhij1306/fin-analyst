"""`fin ingest` and `fin ingest-batch` on the synthetic documents, checked against the key."""

import csv
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner, Result

import fin.findings
import fin.ingest
from fin.audit.logger import Run
from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import TEMPLATES_DIR
from fin.paths import company_db_path

runner = CliRunner()
SHORT_STATEMENT = {"balance_sheet": "bs", "profit_and_loss": "pl", "cash_flow": "cf"}


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def ingest(*args: str) -> Result:
    return runner.invoke(app, ["ingest", *args])


def query(root: Path, company: str, sql: str, params: list[Any] | None = None) -> list[Any]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql, params or []).fetchall()


def run_id_of(result: Result) -> str:
    match = re.search(r"run_id (\S+)$", result.stderr, re.MULTILINE)
    assert match is not None, result.stderr
    return match.group(1)


def loaded_facts(root: Path, company: str) -> dict[tuple[str, str], tuple[Any, ...]]:
    """{(file, sheet!cell): (account, fiscal_year, scope, value, confirmed)} for every fact."""
    rows = query(
        root,
        company,
        "SELECT d.file_name, r.sheet || '!' || r.cell, f.account, f.fiscal_year, f.scope, "
        "f.value, f.confirmed FROM facts f JOIN raw_values r USING (raw_id) "
        "JOIN documents d ON d.doc_id = f.doc_id",
    )
    facts = {(row[0], row[1]): row[2:] for row in rows}
    assert len(facts) == len(rows), "two facts trace to the same cell"
    return facts


def expected_facts(answer_key: dict[str, Any], company: str) -> dict[tuple[str, str], tuple]:
    """The same shape, from the answer key's locations and facts."""
    key = answer_key["companies"][company]
    expected = {}
    for document in key["documents"]:
        file_name = Path(document["file"]).name
        by_scope = document["locations"]
        if isinstance(document["scope"], str):
            by_scope = {document["scope"]: by_scope}
        for scope, years in by_scope.items():
            for year, accounts in years.items():
                for account, location in accounts.items():
                    value = Decimal(key["facts"][scope][year][account]).quantize(Decimal("0.01"))
                    expected[(file_name, location)] = (account, year, scope, value, True)
    return expected


@pytest.mark.parametrize(("company", "documents"), [("kestrel", 1), ("bluepeak", 3)])
def test_excel_facts_equal_the_answer_key_and_trace_to_their_cell(
    root: Path, samples: Path, answer_key: dict[str, Any], company: str, documents: int
) -> None:
    result = ingest(company, str(samples / "clean" / company))

    assert result.exit_code == 0, result.output
    assert f"summary: loaded {documents}, skipped 0, failed 0, pending review 0" in result.stderr
    assert loaded_facts(root, company) == expected_facts(answer_key, company)
    raw_units = query(root, company, "SELECT DISTINCT raw_unit, rupees_per_unit FROM raw_values")
    assert raw_units == (
        [("(₹ in Lakhs)", 100000)] if company == "kestrel" else [("(Rs. in Crores)", 10000000)]
    )


def test_pdf_facts_equal_the_answer_key_with_page_and_notes(
    root: Path, samples: Path, answer_key: dict[str, Any]
) -> None:
    result = ingest("tarang", str(samples / "clean" / "tarang"))

    assert result.exit_code == 0, result.output
    key = answer_key["companies"]["tarang"]
    with (TEMPLATES_DIR / "schedule_iii.csv").open(encoding="utf-8") as handle:
        statement_of = {row["account"]: row["statement"] for row in csv.DictReader(handle)}
    expected = set()
    for document in key["documents"]:
        for year in document["fiscal_years"]:
            for account, value in key["facts"]["standalone"][year].items():
                page = document["pages"][SHORT_STATEMENT[statement_of[account]]]
                expected.add(
                    (Path(document["file"]).name, page, account, year, Decimal(value), True)
                )
    rows = query(
        root,
        "tarang",
        "SELECT d.file_name, r.page, f.account, f.fiscal_year, f.value, f.confirmed "
        "FROM facts f JOIN raw_values r USING (raw_id) JOIN documents d ON d.doc_id = f.doc_id "
        "WHERE r.bbox IS NOT NULL",
    )
    assert len(rows) == len(expected) == 216
    assert set(rows) == expected
    notes = query(root, "tarang", "SELECT section, markdown_path FROM notes ORDER BY markdown_path")
    stem = "Tarang_Industrial_Services_Annual_Report_FY2024-25"
    assert notes[-2:] == [
        ("auditor_report", f"notes/tarang/{stem}.auditor_report.md"),
        ("notes", f"notes/tarang/{stem}.notes.md"),
    ]
    assert (root / notes[-1][1]).read_text(encoding="utf-8").startswith(f"Source: {stem}.pdf")


def test_original_is_archived_by_sha256(root: Path, samples: Path) -> None:
    source = samples / "clean" / "kestrel" / "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
    ingest("kestrel", str(source))
    [(sha256, archive)] = query(root, "kestrel", "SELECT sha256, archive_path FROM documents")
    assert archive == f"archive/kestrel/{sha256}.xlsx"
    assert (root / archive).read_bytes() == source.read_bytes()
    assert source.exists()


def test_default_path_is_the_company_inbox(root: Path, samples: Path) -> None:
    inbox = root / "inbox" / "kestrel"
    inbox.mkdir()
    shutil.copy(samples / "clean" / "kestrel" / "Kestrel_Forgings_Financials_FY23-FY25.xlsx", inbox)
    result = ingest("kestrel")
    assert result.exit_code == 0
    assert result.stdout.splitlines()[-2:] == [
        "[1/1] kestrel/Kestrel_Forgings_Financials_FY23-FY25.xlsx: loaded as doc_id 1 "
        "version 1: 162 facts, 0 unmapped labels",
        "metrics: shared/analytics.duckdb refreshed, 1 companies, 123 metric rows",
    ]


def test_rerun_skips_duplicates_with_e105_and_force_loads_a_new_version(
    root: Path, samples: Path, audit_query: Any
) -> None:
    folder = str(samples / "clean" / "kestrel")
    ingest("kestrel", folder)

    again = ingest("kestrel", folder)

    assert again.exit_code == 0
    assert "summary: loaded 0, skipped 1, failed 0" in again.stderr
    assert audit_query(
        "SELECT level, error_code FROM events WHERE run_id = ? AND stage = 'ingest'",
        [run_id_of(again)],
    ) == [("warning", "E105")]
    assert query(root, "kestrel", "SELECT count(*) FROM facts") == [(162,)]

    forced = ingest("kestrel", folder, "--force")

    assert forced.exit_code == 0
    assert query(
        root, "kestrel", "SELECT doc_id, version, status, superseded_by FROM documents ORDER BY 1"
    ) == [(1, 1, "superseded", 2), (2, 2, "loaded", None)]
    current = query(
        root,
        "kestrel",
        "SELECT count(*) FROM facts JOIN documents USING (doc_id) WHERE status = 'loaded'",
    )
    assert current == [(162,)]


def test_batch_killed_midway_resumes_without_duplicates(
    root: Path, samples: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = root / "batch"
    shutil.copytree(samples / "clean" / "bluepeak", batch / "bluepeak")
    shutil.copytree(samples / "clean" / "kestrel", batch / "kestrel")
    calls = []

    def crash_on_second_document(
        run: Run, company: str, con: duckdb.DuckDBPyConnection, doc_id: int
    ) -> list[dict[str, Any]]:
        calls.append(doc_id)
        if len(calls) == 2:
            raise RuntimeError("simulated kill")
        return []

    monkeypatch.setattr(fin.findings, "VALIDATORS", [crash_on_second_document])
    killed = runner.invoke(app, ["ingest-batch", str(batch)])
    assert isinstance(killed.exception, RuntimeError)
    assert query(root, "bluepeak", "SELECT file_name FROM documents") == [
        ("Bluepeak_Polymers_FY2022-23.xlsx",)
    ]
    assert not (root / "companies" / "kestrel.duckdb").exists()

    monkeypatch.setattr(fin.findings, "VALIDATORS", [])
    resumed = runner.invoke(app, ["ingest-batch", str(batch)])

    assert resumed.exit_code == 0, resumed.output
    assert [re.sub(r": .*", "", line) for line in resumed.stdout.splitlines()] == [
        "[1/4] bluepeak/Bluepeak_Polymers_FY2022-23.xlsx",
        "[2/4] bluepeak/Bluepeak_Polymers_FY2023-24.xlsx",
        "[3/4] bluepeak/Bluepeak_Polymers_FY2024-25.xlsx",
        "[4/4] kestrel/Kestrel_Forgings_Financials_FY23-FY25.xlsx",
        "metrics",
    ]
    assert "skipped (E105" in resumed.stdout.splitlines()[0]
    assert "summary: loaded 3, skipped 1, failed 0" in resumed.stderr
    assert query(root, "bluepeak", "SELECT count(*), count(DISTINCT sha256) FROM documents") == [
        (3, 3)
    ]
    for company, facts in (("bluepeak", 600), ("kestrel", 162)):
        assert query(
            root,
            company,
            "SELECT count(*), count(DISTINCT (doc_id, sheet, cell)) FROM raw_values",
        ) == [(facts, facts)]


def test_a_finding_holds_the_document_with_every_fact(
    root: Path, samples: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unbalanced(
        run: Run, company: str, con: duckdb.DuckDBPyConnection, doc_id: int
    ) -> list[dict[str, Any]]:
        message = "FY25 assets exceed liabilities by 5000000.00"
        return [{"doc_id": doc_id, "check_name": "balance_sheet", "code": "E401"}
                | {"finding": "differ", "message": message}]  # fmt: skip

    monkeypatch.setattr(fin.findings, "VALIDATORS", [unbalanced])
    result = ingest("kestrel", str(samples / "clean" / "kestrel"))

    assert result.exit_code == 0, result.output
    assert "held as doc_id 1 version 1: 162 facts, 0 unmapped labels, 1 findings" in result.stdout
    assert "failed 0, pending review 0, held 1" in result.stderr
    assert query(root, "kestrel", "SELECT status FROM documents") == [("held",)]
    assert query(root, "kestrel", "SELECT count(*) FROM facts") == [(162,)]
    assert query(root, "kestrel", "SELECT code, message, status FROM findings") == [
        ("E401", "FY25 assets exceed liabilities by 5000000.00", "open")
    ]
    assert not (root / "review" / "kestrel").exists()


def test_broken_files_go_to_review_with_their_code(root: Path, samples: Path) -> None:
    folder = root / "drop"
    folder.mkdir()
    for name in (
        "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx",
        "Tarang_FY2024-25_ENCRYPTED.pdf",
        "Tarang_FY2024-25_SCANNED.pdf",
    ):
        shutil.copy(samples / "broken" / name, folder)
    (folder / "notes.csv").write_text("a,b\n", encoding="utf-8")

    result = ingest("bluepeak", str(folder))

    assert result.exit_code == 1
    codes = {
        path.name.removesuffix(".error.json"): json.loads(path.read_text(encoding="utf-8"))["code"]
        for path in (root / "review" / "bluepeak").glob("*.error.json")
    }
    assert codes == {
        "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx": "E203",
        "Tarang_FY2024-25_ENCRYPTED.pdf": "E102",
        "Tarang_FY2024-25_SCANNED.pdf": "E104",
        "notes.csv": "E103",
    }
    assert "summary: loaded 0, skipped 0, failed 4 (E102, E103, E104, E203)," in result.stderr
    assert query(root, "bluepeak", "SELECT count(*) FROM documents") == [(0,)]


def test_unmapped_label_loads_unconfirmed_and_logs_e301(
    root: Path, samples: Path, answer_key: dict[str, Any], audit_query: Any
) -> None:
    facts = answer_key["companies"]["bluepeak"]["facts"]["standalone"]
    book = load_workbook(samples / "clean" / "bluepeak" / "Bluepeak_Polymers_FY2024-25.xlsx")
    assert book["Standalone BS"]["A23"].value.strip() == "Sundry debtors"
    book["Standalone BS"]["A23"] = "Debtors outstanding over six months"
    path = root / "edited.xlsx"
    book.save(path)

    result = ingest("bluepeak", str(path))

    assert result.exit_code == 0
    assert "pending review 1" in result.stderr
    assert query(
        root,
        "bluepeak",
        "SELECT r.cell, f.account, f.mapping_id, f.confirmed, f.value FROM facts f "
        "JOIN raw_values r USING (raw_id) WHERE f.account IS NULL OR NOT f.confirmed ORDER BY 1",
    ) == [
        ("B23", None, None, False, Decimal(facts["FY25"]["trade_receivables"])),
        ("C23", None, None, False, Decimal(facts["FY24"]["trade_receivables"])),
    ]
    assert audit_query(
        "SELECT error_code, message FROM events WHERE run_id = ? AND stage = 'map'",
        [run_id_of(result)],
    ) == [
        (
            "E301",
            "balance_sheet: no dictionary mapping for 'Debtors outstanding over six months'; "
            "loaded unconfirmed",
        )
    ]


def test_bad_company_name_is_a_usage_error(root: Path) -> None:
    result = ingest("Kestrel Forgings")
    assert result.exit_code == 2
    assert "lower-case letters" in result.output
