"""`fin sql` (read, --evidence) and `fin sql --write` on the ingested synthetic documents.

Expected values come from answer_key.json; every answer gets a working evidence page.
"""

import html
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.conn import connect
from fin.paths import analytics_db_path, audit_db_path, company_db_path

runner = CliRunner()
SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "sample-financials"
KEY = json.loads((SAMPLES / "answer_key.json").read_text(encoding="utf-8"))

KESTREL = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
BLUEPEAK_25 = "Bluepeak_Polymers_FY2024-25.xlsx"
BLUEPEAK_23 = "Bluepeak_Polymers_FY2022-23.xlsx"
TARANG_24 = "Tarang_Industrial_Services_Annual_Report_FY2023-24.pdf"
TARANG_25 = "Tarang_Industrial_Services_Annual_Report_FY2024-25.pdf"

FACTS_SQL = (
    "SELECT f.fact_id, f.account, f.fiscal_year, f.scope, f.value {extra} "
    "FROM facts f JOIN documents d USING (doc_id) "
    "WHERE d.status = 'loaded' AND d.file_name = '{file}' AND f.scope = '{scope}' "
    "AND f.fiscal_year = '{year}' AND f.account IN ({accounts}) ORDER BY f.account"
)


def facts_sql(file: str, scope: str, year: str, accounts: list[str], extra: str = "") -> str:
    names = ", ".join(f"'{account}'" for account in accounts)
    return FACTS_SQL.format(extra=extra, file=file, scope=scope, year=year, accounts=names)


def fact(company: str, scope: str, year: str, account: str) -> Decimal:
    return Decimal(KEY["companies"][company]["facts"][scope][year][account])


def location(company: str, file: str, scope: str, year: str, account: str) -> str:
    """sheet!cell from the answer key."""
    for document in KEY["companies"][company]["documents"]:
        if Path(document["file"]).name == file:
            by_scope = document["locations"]
            if isinstance(document["scope"], str):
                by_scope = {document["scope"]: by_scope}
            return by_scope[scope][year][account]
    raise AssertionError(f"{file} not in the answer key")


# (company, question, file, scope, year, accounts, extra SQL column, PDF page per account)
QUESTIONS: list[tuple[str, str, str, str, str, list[str], str, dict[str, int]]] = [
    ("kestrel", "Kestrel revenue in FY25?", KESTREL, "standalone", "FY25", ["revenue"], "", {}),
    ("kestrel", "Kestrel cash at end of FY23?", KESTREL, "standalone", "FY23", ["cash"], "", {}),
    (
        "kestrel",
        "Kestrel debt to equity in FY25?",
        KESTREL,
        "standalone",
        "FY25",
        ["lt_borrowings", "st_borrowings", "total_equity"],
        ", sum(f.value) FILTER (WHERE f.account <> 'total_equity') OVER () "
        "/ sum(f.value) FILTER (WHERE f.account = 'total_equity') OVER () AS debt_to_equity",
        {},
    ),
    (
        "bluepeak",
        "Bluepeak consolidated revenue in FY25?",
        BLUEPEAK_25,
        "consolidated",
        "FY25",
        ["revenue"],
        "",
        {},
    ),
    (
        "bluepeak",
        "Bluepeak standalone total borrowings at end of FY25?",
        BLUEPEAK_25,
        "standalone",
        "FY25",
        ["lt_borrowings", "st_borrowings"],
        ", sum(f.value) OVER () AS total_borrowings",
        {},
    ),
    (
        "bluepeak",
        "Bluepeak standalone trade receivables at end of FY22?",
        BLUEPEAK_23,
        "standalone",
        "FY22",
        ["trade_receivables"],
        "",
        {},
    ),
    (
        "bluepeak",
        "Bluepeak standalone PAT in FY23?",
        BLUEPEAK_23,
        "standalone",
        "FY23",
        ["pat"],
        "",
        {},
    ),
    (
        "tarang",
        "Tarang revenue in FY25?",
        TARANG_25,
        "standalone",
        "FY25",
        ["revenue"],
        "",
        {"revenue": 4},
    ),
    (
        "tarang",
        "Tarang cash at end of FY23?",
        TARANG_24,
        "standalone",
        "FY23",
        ["cash"],
        "",
        {"cash": 3},
    ),
    (
        "tarang",
        "Tarang revenue and cash in FY24?",
        TARANG_25,
        "standalone",
        "FY24",
        ["cash", "revenue"],
        "",
        {"cash": 3, "revenue": 4},
    ),
]


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture(scope="module")
def ingested(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A data folder with the clean synthetic documents of all three companies ingested."""
    root = (tmp_path_factory.mktemp("query") / "FinAnalysis").resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIN_DATA_DIR", str(root))
        assert invoke("init").exit_code == 0
        for company in ("kestrel", "bluepeak", "tarang"):
            result = invoke("ingest", company, str(SAMPLES / "clean" / company))
            assert result.exit_code == 0, result.output
        yield root


def audit_rows(root: Path, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    with connect(audit_db_path(root)) as con:
        return con.execute(sql, params).fetchall()


@pytest.mark.parametrize(
    ("company", "question", "file", "scope", "year", "accounts", "extra", "pages"),
    QUESTIONS,
    ids=[q[1] for q in QUESTIONS],
)
def test_question_answers_from_the_key_with_an_evidence_page(
    ingested: Path,
    company: str,
    question: str,
    file: str,
    scope: str,
    year: str,
    accounts: list[str],
    extra: str,
    pages: dict[str, int],
) -> None:
    sql = facts_sql(file, scope, year, accounts, extra)
    result = invoke("sql", company, "--question", question, "--sql", sql, "--evidence")
    assert result.exit_code == 0, result.output
    answer = json.loads(result.stdout)

    expected = {a: fact(company, scope, year, a) for a in accounts}
    assert {row["account"]: Decimal(row["value"]) for row in answer["rows"]} == expected
    refs = {
        a: f"page {pages[a]}" if pages else location(company, file, scope, year, a)
        for a in accounts
    }
    assert {r["account"]: (r["source_file"], r["source_ref"]) for r in answer["rows"]} == {
        a: (file, ref) for a, ref in refs.items()
    }
    assert all(row["source_confirmed"] is True for row in answer["rows"])
    if "debt_to_equity" in extra:
        debt = expected["lt_borrowings"] + expected["st_borrowings"]
        ratio = float(debt / expected["total_equity"])
        assert answer["rows"][0]["debt_to_equity"] == pytest.approx(ratio)
        metric = KEY["companies"][company]["metrics"][scope][year]["debt_to_equity"]
        assert answer["rows"][0]["debt_to_equity"] == pytest.approx(metric)
    if "total_borrowings" in extra:
        total = expected["lt_borrowings"] + expected["st_borrowings"]
        assert Decimal(answer["rows"][0]["total_borrowings"]) == total

    [(row_count, result_hash, mode)] = audit_rows(
        ingested,
        "SELECT row_count, result_hash, mode FROM queries WHERE query_id = ?",
        [answer["query_id"]],
    )
    assert (row_count, result_hash, mode) == (len(accounts), answer["result_hash"], "read")

    page_path = ingested / "evidence" / f"{answer['query_id']}.html"
    assert answer["evidence_path"] == f"evidence/{answer['query_id']}.html"
    page = page_path.read_text(encoding="utf-8")
    assert question in page
    for row in answer["rows"]:
        assert html.escape(f"{file}, {refs[row['account']]}") in page
        if pages:
            crop = page_path.parent / answer["query_id"] / f"fact_{row['fact_id']}.png"
            assert crop.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            assert f"src='{answer['query_id']}/fact_{row['fact_id']}.png'" in page
        else:
            assert "<table class='grid'>" in page
    if not pages:
        assert page.count("<td class='hit'>") == len(accounts)
    [(evidence_path,)] = audit_rows(
        ingested,
        "SELECT evidence_path FROM queries WHERE query_id = ? AND evidence_path IS NOT NULL",
        [answer["query_id"]],
    )
    assert evidence_path == f"evidence/{answer['query_id']}.html"


def test_excel_grid_shows_the_label_and_value_around_the_cell(ingested: Path) -> None:
    sql = facts_sql(KESTREL, "standalone", "FY25", ["revenue"])
    answer = json.loads(invoke("sql", "kestrel", "--sql", sql, "--evidence").stdout)
    page = (ingested / "evidence" / f"{answer['query_id']}.html").read_text(encoding="utf-8")
    grid = page[page.index("<table class='grid'>") :]
    # Profit and Loss!C7 in lakhs, with its row label in column A and the rows around it.
    assert re.search(
        r"<tr><th>7</th><td>[^<]*Revenue[^<]*</td><td>[^<]*</td><td class='hit'>", grid
    )
    assert "<tr><th>5</th>" in grid
    assert "<tr><th>9</th>" in grid


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE facts SET value = 0",
        "DELETE FROM facts",
        "SELECT 1; DELETE FROM facts",
        "COPY facts TO 'out.csv'",
        "ATTACH 'other.duckdb'",
    ],
)
def test_query_rejects_anything_but_one_select_and_logs_it(ingested: Path, sql: str) -> None:
    result = invoke("sql", "kestrel", "--question", "Zero all values", "--sql", sql)
    assert result.exit_code == 2
    assert result.stderr.startswith("E502: without --write, `fin sql` runs one SELECT statement")
    run_id = re.search(r"run_id (\S+)$", result.stderr, re.MULTILINE).group(1)  # type: ignore[union-attr]
    assert audit_rows(
        ingested,
        "SELECT sql, mode, row_count FROM queries WHERE query_id = ?",
        [run_id],
    ) == [(sql, "read", None)]
    assert audit_rows(
        ingested, "SELECT error_code FROM events WHERE run_id = ? AND level = 'error'", [run_id]
    ) == [("E502",)]
    with connect(company_db_path(ingested, "kestrel")) as con:
        assert con.execute("SELECT count(*) FROM facts").fetchone() == (162,)
        revenue = "SELECT value FROM facts WHERE account = 'revenue' AND fiscal_year = 'FY25'"
        assert con.execute(revenue).fetchall() == [
            (fact("kestrel", "standalone", "FY25", "revenue"),)
        ]


def test_query_with_a_bad_column_fails_with_e504(ingested: Path) -> None:
    result = invoke("sql", "kestrel", "--sql", "SELECT nope FROM facts")
    assert result.exit_code == 2
    assert result.stderr.startswith("E504: the query failed")


def test_evidence_flags_a_missing_source_file_with_e604(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "FinAnalysis").resolve()
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    invoke("init")
    invoke("ingest", "tarang", str(SAMPLES / "clean" / "tarang" / TARANG_25))
    sql = facts_sql(TARANG_25, "standalone", "FY25", ["revenue"])
    for archived in (root / "archive" / "tarang").iterdir():
        archived.unlink()

    result = invoke("sql", "tarang", "--sql", sql, "--evidence")

    assert result.exit_code == 1
    answer = json.loads(result.stdout)
    page = (root / "evidence" / f"{answer['query_id']}.html").read_text(encoding="utf-8")
    assert f"<p class='flag'>E604: {TARANG_25}: archived source archive/tarang/" in page
    assert f"{TARANG_25}, page 4" in page


@pytest.fixture
def kestrel(data_root: Path) -> Path:
    invoke("init")
    assert invoke("ingest", "kestrel", str(SAMPLES / "clean" / "kestrel")).exit_code == 0
    return data_root


def test_sql_write_changes_a_value_with_backup_and_audit_row(kestrel: Path) -> None:
    sql = "UPDATE facts SET value = 1.00 WHERE account = 'revenue' AND fiscal_year = 'FY25'"

    result = invoke("sql", "kestrel", "--write", "--sql", sql)

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["rows_affected"] == 1
    assert out["backup_path"] == f"backups/{out['query_id']}/companies/kestrel.duckdb"
    with connect(company_db_path(kestrel, "kestrel")) as con:
        changed = "SELECT value FROM facts WHERE account = 'revenue' AND fiscal_year = 'FY25'"
        assert con.execute(changed).fetchall() == [(Decimal("1.00"),)]
    with connect(kestrel / out["backup_path"]) as con:
        assert con.execute(changed).fetchall() == [
            (fact("kestrel", "standalone", "FY25", "revenue"),)
        ]
    assert audit_rows(
        kestrel,
        "SELECT sql, mode, row_count, backup_path FROM queries WHERE query_id = ?",
        [out["query_id"]],
    ) == [(sql, "write", 1, out["backup_path"])]
    # Metrics are refreshed: revenue of 1.00 makes FY25 receivable days 365 x receivables / 1.00.
    with connect(analytics_db_path(kestrel)) as con:
        assert con.execute(
            "SELECT value FROM metrics WHERE company = 'kestrel' AND fiscal_year = 'FY25' "
            "AND metric = 'receivable_days'"
        ).fetchall() == [
            (Decimal(365) * fact("kestrel", "standalone", "FY25", "trade_receivables"),)
        ]


def test_sql_write_rolls_back_every_statement_when_one_fails(kestrel: Path) -> None:
    sql = "UPDATE facts SET value = 999; UPDATE facts SET nope = 1"

    result = invoke("sql", "kestrel", "--write", "--sql", sql)

    assert result.exit_code == 2
    assert result.stderr.startswith("E504: the change failed and was rolled back")
    with connect(company_db_path(kestrel, "kestrel")) as con:
        assert con.execute("SELECT count(*) FROM facts WHERE value = 999").fetchone() == (0,)


def test_sql_write_records_the_question(kestrel: Path) -> None:
    sql = "UPDATE facts SET value = 1.00 WHERE account = 'revenue' AND fiscal_year = 'FY25'"
    out = json.loads(invoke("sql", "kestrel", "--write", "--question", "fix", "--sql", sql).stdout)
    assert audit_rows(
        kestrel, "SELECT question FROM queries WHERE query_id = ?", [out["query_id"]]
    ) == [("fix",)]
