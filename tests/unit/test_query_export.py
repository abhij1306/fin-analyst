"""The query export (fin.diligence.export) on the synthetic set of tests/recon_fixtures.py.

The round trip draft -> approve -> export -> answer -> adjustment -> G4, real names only in the
exported files, tokens everywhere fin prints or logs."""

import csv
import shutil
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from openpyxl import load_workbook
from typer.testing import CliRunner

from fin.actor import USER
from fin.audit.errors import NothingToExport, QueryPeriodInvalid, ReplyFileError, UnknownToken
from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import gates, ledgers, payroll, queries
from fin.diligence.answers import read_reply
from fin.diligence.export import export_queries
from fin.diligence.export_data import Issue, line_difference
from fin.diligence.export_xlsx import source_marks
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from fin.reconcile import items, run_new
from tests.recon_fixtures import ACCOUNTS, write_set
from tests.tally_fixtures import CUSTOMERS

runner = CliRunner()


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "acme"), "company")
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    monkeypatch.setenv("FIN_DATA_DIR", str(data))
    ensure_database(audit_db_path(data), "audit")
    (data / "logs").mkdir()
    with connect(company_db_path(data, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    files = write_set(tmp_path)
    for key, kind in (
        ("tb24", "trial_balance"),
        ("tb25", "trial_balance"),
        ("lv", "ledger_vouchers"),
        ("registers", "registers"),
        ("payroll", "payroll"),
    ):
        ingest_source(run(data), load_config(data), "acme", files[key], kind, None)
    proposals = [
        {"ledger": k, "account": v, "confidence": 0.9, "reason": "by name"}
        for k, v in ACCOUNTS.items()
    ]
    ledgers.propose(run(data), "acme", proposals)
    ids = [r["ledger_id"] for r in ledgers.list_ledgers(run(data), "acme")]
    ledgers.confirm(run(data), "acme", ids, actor=USER)
    payroll.confirm_unit(run(data), "acme", "payroll.xlsx", "annual")
    return data


def fin(*args: str) -> Any:
    return runner.invoke(app, list(args))


def token_of(root: Path, real: str) -> str:
    with connect(company_db_path(root, "acme")) as con:
        row = con.execute("SELECT token FROM mask_values WHERE real_value = ?", [real]).fetchone()
    assert row is not None
    return str(row[0])


def raise_issue(root: Path) -> int:
    """An issue of the two sales lines at or above the threshold, drafted with tokens."""
    [result] = run_new(run(root), "acme", "sales_register_vs_gl", "FY25")
    ids = [x["item_id"] for x in result["lines"] if x["needs_query"]]
    token = token_of(root, CUSTOMERS[0])
    query, _ = items.raise_from_items(
        run(root), "acme", ids, f"Please explain these invoices, including those of {token}."
    )
    return int(query["query_id"])


def test_a_foreign_currency_line_is_valued_in_rupees_everywhere() -> None:
    usd = {
        "kind": "difference",
        "currency": "USD",
        "amount_inr": D("-83000.00"),
        "difference": D("-1000"),
    }
    no_rate = {"kind": "difference", "currency": "USD", "amount_inr": None, "difference": D("500")}
    inr = {"kind": "left_only", "currency": None, "amount_inr": None, "difference": D("120000")}

    totals = items.kind_totals([usd, no_rate, inr, inr], lambda line: line["kind"])

    assert totals == {"difference": [2, D("83000.00")], "left_only": [2, D("240000")]}
    assert items.kind_totals([no_rate], lambda line: line["kind"]) == {"difference": [1, None]}
    assert items.kind_text(1, "difference", None) == "1 difference with no rupee value"
    assert line_difference(usd) == "-₹83,000.00"
    assert line_difference(no_rate) == "USD 500"


def exported_paths(root: Path, output: str) -> list[Path]:
    return [Path(x) for x in output.splitlines() if x.startswith(str(root / "exports"))]


def texts(path: Path) -> str:
    book = load_workbook(path)
    return "\n".join(
        str(cell.value) for sheet in book for row in sheet.iter_rows() for cell in row if cell.value
    )


def all_files_text(folder: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in folder.rglob("*") if p.is_file()
    )


def test_round_trip_with_real_names_only_in_the_export(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query_id = raise_issue(root)
    real = CUSTOMERS[0]
    approved = fin("query", "approve", "acme", str(query_id))
    assert approved.exit_code == 0, approved.output
    assert "approved" in approved.output, approved.output

    exported = fin("query", "export", "acme")

    assert exported.exit_code == 0, exported.output
    paths = exported_paths(root, exported.output)
    assert [p.name for p in paths] == ["queries.xlsx", "email.txt"]
    assert all(p.is_file() and root / "exports" / "acme" in p.parents for p in paths)
    # fin prints only the paths: no name, no token
    assert real not in exported.output
    assert "CUST-" not in exported.output
    book = load_workbook(paths[0])
    assert book.sheetnames[:2] == ["Summary", f"Q{query_id}"]
    assert any(name.startswith("S1 ") for name in book.sheetnames)
    content = texts(paths[0]) + paths[1].read_text(encoding="utf-8")
    assert real in content
    assert "CUST-" not in content
    assert "VEND-" not in content
    assert "1 only in the register" in content
    assert "1 only in the books" in content
    source = next(book[n] for n in book.sheetnames if n.endswith("Sales Register"))
    marked = [r for r in source.iter_rows() if r[0].fill.start_color.rgb == "00FFFF00"]
    assert len(marked) == 1
    assert any(f"Q{query_id}" in str(c.value) for c in source[marked[0][0].row])
    # the query and its lines carry the register token, never the name: the export alone
    # writes it (ledgers keep names as filed, so the mapping log names the customer's ledger)
    with connect(company_db_path(root, "acme")) as con:
        for table in ("accountant_queries", "recon_items", "query_items"):
            assert real not in str(con.execute(f"SELECT * FROM {table}").fetchall())
    logged = all_files_text(root / "logs").splitlines()
    assert not [line for line in logged if real in line and "mapping_decisions" not in line]

    # the user sends it, then the accountant replies with a file
    assert fin("query", "sent", "acme", str(query_id)).exit_code == 0
    reply = tmp_path / "reply.txt"
    reply.write_text(f"{real} confirms INV-2003 was billed in the next year.", encoding="utf-8")
    answered = fin("query", "answer", "acme", str(query_id), "--file", str(reply))
    assert answered.exit_code == 0, answered.output
    assert f"{real} confirms" in answered.output  # the reply is stored as written
    assert list((root / "archive" / "acme").glob("*.txt"))

    # an adjustment on the answered query, then G4's checklist
    monkeypatch.setattr(gates, "require", lambda *args: None)  # G1/G2 approval: test_gates
    proposed = fin(
        "adjust", "propose", "acme", "--period", "FY25", "--scope", "standalone",
        "--category", "error", "--amount", "-400000", "--description", "Billed in the next year",
        "--query", str(query_id),
    )  # fmt: skip
    assert proposed.exit_code == 0, proposed.output
    assert fin("adjust", "accept", "acme", "1").exit_code == 0
    with connect(company_db_path(root, "acme")) as con:
        g4 = next(g for g in gates.evaluate(gates.context(con, root, "acme")) if g["gate"] == "G4")
    assert all(line["ok"] for line in g4["checklist"])


def test_brief_is_a_word_file_per_issue(root: Path) -> None:
    query_id = raise_issue(root)
    fin("query", "approve", "acme", str(query_id))

    exported = fin("query", "export", "acme", "--brief")

    [path] = exported_paths(root, exported.output)
    assert path.name == f"query-{query_id}.docx"
    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        c.text for t in doc.tables for r in t.rows for c in r.cells
    )
    assert CUSTOMERS[0] in text
    assert "CUST-" not in text
    assert "only in the register" in text


def test_nothing_approved_and_unknown_tokens_are_refused(root: Path) -> None:
    raise_issue(root)
    context = run(root)
    with pytest.raises(NothingToExport):
        export_queries(context, "acme")

    # a token no masked name has is refused when the query is drafted (test run 2026-11-25: it
    # surfaced only at export, after the user had approved it)
    manual = {"amount": D(1), "scope": None, "sources": "x"}
    with pytest.raises(UnknownToken, match="CUST-0099"):
        queries.raise_manual(context, "acme", period="FY25", question="About CUST-0099?", **manual)
    with pytest.raises(UnknownToken, match="CUST-0098"):
        queries.raise_manual(
            context, "acme", period="FY25", question="About?", **(manual | {"sources": "CUST-0098"})
        )
    with pytest.raises(QueryPeriodInvalid, match="FY99"):
        queries.raise_manual(context, "acme", period="FY99", question="?", **manual)
    query = queries.raise_manual(context, "acme", period="FY25", question="About?", **manual)
    query_id = query["query_id"]
    with pytest.raises(UnknownToken, match="CUST-0099"):
        queries.edit(context, "acme", query_id, "About CUST-0099?")
    # one that reaches the export anyway (written outside fin) is still refused there
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE accountant_queries SET question = 'About CUST-0099?', status = 'approved' "
            "WHERE query_id = ?",
            [query["query_id"]],
        )
    with pytest.raises(UnknownToken, match="CUST-0099"):
        export_queries(context, "acme")
    assert not (root / "exports" / "acme").exists() or not any(
        (root / "exports" / "acme").iterdir()
    )


def test_a_reply_file_that_is_not_text_needs_text(root: Path, tmp_path: Path) -> None:
    query_id = raise_issue(root)
    fin("query", "approve", "acme", str(query_id))
    fin("query", "sent", "acme", str(query_id))
    pdf = tmp_path / "reply.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    refused = fin("query", "answer", "acme", str(query_id), "--file", str(pdf))
    ok = fin("query", "answer", "acme", str(query_id), "--file", str(pdf), "--text", "see reply")

    assert refused.exit_code == 2
    assert "E860" in refused.output
    assert ok.exit_code == 0
    assert "reply.pdf (archive/acme/" in ok.output
    missing = tmp_path / "none.txt"
    with pytest.raises(ReplyFileError):
        read_reply("acme", missing, None)


def test_golden_checks_cover_the_issue_and_each_kind_of_difference(root: Path) -> None:
    query_id = raise_issue(root)
    fin("query", "approve", "acme", str(query_id))
    init_golden(run(root), "acme")
    path = golden_path(root, "acme")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["kind"].startswith("query_")]
    assert {r["ledger"] for r in rows} == {
        str(query_id),
        f"{query_id} left_only",
        f"{query_id} right_only",
    }
    expected = {str(query_id): "550000", f"{query_id} left_only": "400000"}
    expected[f"{query_id} right_only"] = "150000"
    for row in rows:
        row.update(expected=expected[row["ledger"]], checked="yes")
    rows[0]["expected"] = "1" if rows[0]["ledger"] == str(query_id) else rows[0]["expected"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*COLUMNS, "note"])
        writer.writeheader()
        writer.writerows(rows)

    result = run_eval(run(root), "acme")

    outcome = {c["ledger"]: c["outcome"] for c in result["checks"]}
    assert outcome[str(query_id)] == "fail"  # the planted wrong value
    assert outcome[f"{query_id} left_only"] == outcome[f"{query_id} right_only"] == "pass"


def test_a_reused_file_name_resolves_to_the_loaded_file_of_the_period() -> None:
    files = [
        (1, "tb.xlsx", "archive/a.xlsx", "superseded", "FY25"),
        (2, "tb.xlsx", "archive/b.xlsx", "loaded", "FY25"),
        (3, "tb.xlsx", "archive/c.xlsx", "loaded", "FY24"),
        (-1, "Statements.xlsx", "archive/d.xlsx", "loaded", "FY25"),
    ]

    issues = [
        Issue({"query_id": 1, "period": "FY25",
               "sources": "tb.xlsx Sheet 1!C7; Statements.xlsx P&L!C7; bank.pdf page 3"}),
        Issue({"query_id": 2, "period": "FY24", "sources": "tb.xlsx Sheet 1!4, 5 and 2 more"}),
    ]  # fmt: skip

    marks = source_marks(issues, files)

    # The superseded FY25 file and the page of a PDF get no highlight.
    assert dict(marks) == {
        (2, "Sheet 1"): {7: ({"C7"}, {"Q1"})},
        (-1, "P&L"): {7: ({"C7"}, {"Q1"})},
        (3, "Sheet 1"): {4: ({""}, {"Q2"}), 5: ({""}, {"Q2"})},
    }
