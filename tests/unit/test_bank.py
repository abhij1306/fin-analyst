"""Bank statements (fin.diligence.bank): synthetic Excel statements for ICICI, SBI and Axis (INR and
USD) and a synthetic Axis PDF (recorded parser output) load with sheet or page + row lineage and
remarks as filed; a statement that does not tie, a balance gap and overlapping periods are findings;
a PDF is parsed once per sha256, and LlamaParse is never called."""

import dataclasses
import json
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fin.audit.errors import (
    BankHeaderMissing,
    BankPdfUnreadable,
    DuplicateFile,
    LayoutUnknown,
    PdfAddonFailed,
    SourcePeriodInvalid,
)
from fin.audit.logger import Run
from fin.config import Config, load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import bank_checks, bank_pdf, queries
from fin.diligence.bank import ingest_bank
from fin.extract import fallback, llamaparse_run
from fin.extract.addon import PageRead
from fin.extract.pdf_tables import Cell, Table
from fin.extract.profiles import load_profile, profiles_for
from fin.findings import accept
from fin.ingest import sha256_of
from fin.paths import company_db_path
from tests.bank_fixtures import (
    CLOSING,
    CUSTOMER,
    VENDOR,
    pdf_pages,
    recorded_parse,
    write_bank_pdf,
    write_statement,
)

MAY_CLOSING = CLOSING + (CLOSING - 100000)  # May: the default rows again, from the April closing
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bank"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with company acme, an open FY25 engagement and two known register names."""
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
        for token, kind, name in (
            ("CUST-0001", "customer", CUSTOMER),
            ("VEND-0001", "vendor", VENDOR),
        ):
            con.execute(
                "INSERT INTO mask_values VALUES (?, ?, ?, ?, 0, 'r0')",
                [token, kind, name, name.casefold()],
            )
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def load(root: Path, path: Path, parser: str = "none", profile: str | None = None) -> list[str]:
    config: Config = dataclasses.replace(load_config(root), pdf_parser=parser)
    return ingest_bank(Run("ingest", {}, root), config, "acme", path, profile).lines


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def g1(root: Path) -> list[tuple[bool, str]]:
    with connect(company_db_path(root, "acme")) as con:
        ctx = SimpleNamespace(con=con, company="acme", engagement={"engagement_id": 1})
        return bank_checks.checklist(ctx)


def findings(root: Path) -> list[tuple[Any, ...]]:
    return rows(
        root,
        "SELECT check_name, status, file_id, label, difference FROM findings "
        "WHERE status IN ('open', 'accepted') ORDER BY finding_id",
    )


@pytest.mark.parametrize(
    ("bank", "currency"),
    [
        ("icici", "INR"),
        ("icici", "USD"),
        ("sbi", "INR"),
        ("sbi", "USD"),
        ("axis", "INR"),
        ("axis", "USD"),
    ],
)
def test_excel_statements_load_with_lineage_and_remarks_as_filed(
    root: Path, tmp_path: Path, bank: str, currency: str
) -> None:
    lines = load(root, write_statement(tmp_path / "s.xlsx", bank, currency=currency))

    assert lines[0].startswith("loaded as source file_id 1 (bank FY25, profile bank_")
    assert "ties" in lines[1]
    assert "DOES NOT TIE" not in lines[1]
    assert rows(
        root,
        "SELECT bank, account, currency, period_start::VARCHAR, period_end::VARCHAR, "
        "opening_balance, opening_from, closing_balance, line_count, ties, reader, parse_path "
        "FROM bank_statements",
    ) == [
        (
            bank.upper() if bank != "axis" else "Axis",
            "ACCT-0001",
            currency,
            "2024-04-01",
            "2024-04-30",
            Decimal("100000.00"),
            "header",
            Decimal(CLOSING),
            3,
            True,
            "excel",
            None,
        )
    ]
    lines_ = rows(
        root,
        'SELECT sheet, page, "row", txn_date::VARCHAR, remarks, debit, credit, balance '
        'FROM bank_lines ORDER BY "row"',
    )
    assert lines_[0] == (
        "Statement",
        None,
        8,
        "2024-04-03",
        f"NEFT-{CUSTOMER}-INV101",
        Decimal("0.00"),
        Decimal("250000.00"),
        Decimal("350000.00"),
    )
    # bank text is not masked (docs/decisions/masking.md): known names stay as filed
    assert lines_[1][4] == f"IMPS to {VENDOR} rent"
    stored = " ".join(str(r) for r in rows(root, "SELECT * FROM bank_lines"))
    assert "CUST-0001" not in stored
    assert "VEND-0001" not in stored
    assert rows(root, "SELECT kind, period, basis FROM source_files") == [
        ("bank", "FY25", "audited")
    ]
    assert findings(root) == []


def test_a_statement_that_does_not_tie_is_a_finding_and_blocks_g1_until_accepted(
    root: Path, tmp_path: Path
) -> None:
    lines = load(root, write_statement(tmp_path / "s.xlsx", closing=CLOSING + 5000))

    assert "DOES NOT TIE" in lines[1]
    assert findings(root) == [("bank_tie", "open", 1, "ICICI ACCT-0001 INR", Decimal("-5000.00"))]
    assert rows(root, "SELECT ties, line_count FROM bank_statements") == [(False, 3)]
    ok, text = g1(root)[0]
    assert not ok
    assert "1 bank parses do not tie" in text

    accept(Run("accept", {}, root), "acme", [1])
    assert g1(root) == [(True, "every bank parse ties (1 statements)")]


def test_a_balance_gap_between_statements_is_a_finding(root: Path, tmp_path: Path) -> None:
    load(root, write_statement(tmp_path / "apr.xlsx"))
    lines = load(
        root,
        write_statement(tmp_path / "may.xlsx", month=5, last_day=31, opening=CLOSING + 7000),
    )

    assert findings(root) == [
        ("bank_balance_gap", "open", 2, "ICICI ACCT-0001 INR", Decimal("7000.00"))
    ]
    assert "opens at" in lines[-1]
    # each source names its file once (a query raised from it copies them; test run 2026-11-25)
    assert rows(root, "SELECT fin_source, other_file, other_source FROM findings") == [
        ("opening balance", "apr.xlsx", "closing balance")
    ]
    # a query raised from it takes the engagement period, not the statement's dates
    [(finding_id,)] = rows(root, "SELECT finding_id FROM findings")
    query = queries.raise_from_finding(Run("q", {}, root), "acme", finding_id, "Missing?")
    assert (query["period"], query["sources"]) == (
        "FY25",
        "may.xlsx opening balance; apr.xlsx closing balance",
    )


def test_a_continuous_pair_has_no_finding_and_a_missing_month_resolves_a_gap(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_statement(tmp_path / "apr.xlsx"))
    load(root, write_statement(tmp_path / "may.xlsx", month=5, last_day=31, opening=CLOSING))
    assert findings(root) == []
    june = write_statement(tmp_path / "jul.xlsx", month=7, last_day=31, opening=MAY_CLOSING + 1000)
    load(root, june)
    assert [f[0] for f in findings(root)] == ["bank_balance_gap"]
    # June, closing where July opens, joins the two: the gap is resolved
    load(
        root,
        write_statement(
            tmp_path / "jun.xlsx",
            month=6,
            last_day=30,
            opening=MAY_CLOSING,
            rows=[(5, "Interest", 0, 1000)],
        ),
    )
    assert findings(root) == []


def test_overlapping_periods_of_one_account_are_a_finding(root: Path, tmp_path: Path) -> None:
    load(root, write_statement(tmp_path / "a.xlsx"))
    load(root, write_statement(tmp_path / "b.xlsx", rows=[(4, "Cash deposit", 0, 100)]))

    assert findings(root) == [("bank_overlap", "open", 2, "ICICI ACCT-0001 INR", None)]


def test_another_account_does_not_overlap(root: Path, tmp_path: Path) -> None:
    load(root, write_statement(tmp_path / "a.xlsx"))
    load(root, write_statement(tmp_path / "b.xlsx", "sbi", account="998877665544"))
    load(root, write_statement(tmp_path / "c.xlsx", currency="USD"))
    assert findings(root) == []


def test_the_stub_period_is_unaudited_and_a_year_crossing_statement_is_refused(
    root: Path, tmp_path: Path
) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE engagement SET stub = 'FY26-YTD-SEP', stub_start = '2025-04-01', "
            "stub_end = '2025-09-30'"
        )
    lines = load(root, write_statement(tmp_path / "s.xlsx", year=2025, month=5, last_day=31))
    assert "FY26-YTD-SEP is the stub" in lines[0]
    assert rows(root, "SELECT period, basis FROM source_files") == [("FY26-YTD-SEP", "unaudited")]
    with pytest.raises(SourcePeriodInvalid, match="not inside one period"):
        load(root, write_statement(tmp_path / "t.xlsx", year=2024, month=3, last_day=31))
    assert rows(root, "SELECT count(*) FROM source_files") == [(1,)]


def test_a_file_is_loaded_once_and_unknown_layouts_and_headers_are_refused(
    root: Path, tmp_path: Path
) -> None:
    path = write_statement(tmp_path / "s.xlsx")
    load(root, path)
    with pytest.raises(DuplicateFile):
        load(root, path)

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append([f"Statement for {CUSTOMER}"])
    sheet.append(["Account No: 556677889900 (savings)"])
    sheet.append(["When", "Narration", "Money"])
    sheet.append(["01/04/2024", "x", 5])
    book.save(tmp_path / "odd.xlsx")
    with pytest.raises(
        LayoutUnknown, match="no bank column profile|no bank profile|no bank"
    ) as err:
        load(root, tmp_path / "odd.xlsx")
    # the title lines are listed, the account number in them is not (SPEC-17 item 1)
    assert "Account No: [number not shown] (savings)" in err.value.message
    assert "556677889900" not in str(err.value)

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for line in ("Statement of Account From 01-04-2024 To 30-04-2024", "Opening Balance: 5.00"):
        sheet.append([line])
    sheet.append(["Tran Date", "CHQNO", "PARTICULARS", "DR", "CR", "BAL", "SOL"])
    sheet.append(["02-04-2024", "1", "x", None, 5, 10, "1"])
    book.save(tmp_path / "noacct.xlsx")
    with pytest.raises(BankHeaderMissing):
        load(root, tmp_path / "noacct.xlsx")
    assert rows(root, "SELECT count(*) FROM bank_statements") == [(1,)]


def test_a_bank_profile_reads_its_header_patterns() -> None:
    profile = load_profile("bank_icici")
    assert profile.kind == "bank"
    assert profile.bank == "ICICI"
    assert profile.unit == 1
    assert set(profile.header) == {"account", "currency", "opening", "closing"}


# --- PDF ---------------------------------------------------------------------------------------


def test_a_pdf_needs_a_pdf_parser(root: Path, tmp_path: Path) -> None:
    with pytest.raises(BankPdfUnreadable, match="\\[pdf\\] parser is none"):
        load(root, write_bank_pdf(tmp_path / "s.pdf"))
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]


def save_parse(root: Path, pdf: Path, *, misread: bool = False) -> Path:
    saved = bank_pdf.saved_parse(root, sha256_of(pdf))
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(recorded_parse(sha256_of(pdf), misread=misread), encoding="utf-8")
    return saved


def test_a_pdf_statement_loads_from_its_saved_parse_with_page_lineage(
    root: Path, tmp_path: Path
) -> None:
    pdf = write_bank_pdf(tmp_path / "s.pdf")
    save_parse(root, pdf)

    lines = load(root, pdf, parser="liteparse")

    assert "read by liteparse" in lines[0]
    assert "ties" in lines[1]
    assert rows(
        root,
        "SELECT bank, account, reader, parse_path LIKE 'cache/bank/%.json', ties, line_count "
        "FROM bank_statements",
    ) == [("Axis", "ACCT-0001", "liteparse", True, True, 4)]
    assert rows(
        root,
        'SELECT sheet, page, "row", txn_date::VARCHAR, remarks FROM bank_lines '
        'ORDER BY page, "row"',
    ) == [
        ("page 1", 1, 2, "2024-04-02", f"NEFT-{CUSTOMER}-INV202"),
        ("page 1", 1, 3, "2024-04-09", f"RTGS to {VENDOR}"),
        ("page 2", 2, 1, "2024-04-17", "Cheque deposit"),
        ("page 2", 2, 2, "2024-04-25", "Bank charges"),
    ]
    pages = rows(root, 'SELECT page, "row" FROM bank_lines ORDER BY page, "row"')
    assert pages == [(1, 2), (1, 3), (2, 1), (2, 2)]
    assert findings(root) == []


def test_a_saved_parse_with_no_table_says_how_to_parse_again(root: Path, tmp_path: Path) -> None:
    pdf = write_bank_pdf(tmp_path / "s.pdf", "no table")
    saved = bank_pdf.saved_parse(root, sha256_of(pdf))
    saved.parent.mkdir(parents=True, exist_ok=True)
    empty = {"1": {"tables": [], "texts": ["Axis Bank"]}}
    saved.write_text(json.dumps({"engine": "liteparse", "sha256": "x", "pages": empty}))

    with pytest.raises(BankPdfUnreadable, match="delete the saved parse first"):
        load(root, pdf, parser="liteparse")


def test_a_pdf_parse_that_does_not_tie_is_a_finding(root: Path, tmp_path: Path) -> None:
    pdf = write_bank_pdf(tmp_path / "s.pdf", "second copy")
    save_parse(root, pdf, misread=True)

    lines = load(root, pdf, parser="liteparse")

    assert "DOES NOT TIE" in lines[1]
    assert findings(root) == [("bank_tie", "open", 1, "Axis ACCT-0001 INR", Decimal("27000.00"))]


def fake_engine(calls: list[int]) -> Any:
    def convert(request: Any, notices: list[str]) -> dict[int, PageRead]:
        calls.append(1)
        found = {}
        for number, body in pdf_pages().items():
            tables = [Table([[Cell(c) for c in row] for row in g]) for g in body["tables"]]  # type: ignore[attr-defined]
            found[int(number)] = PageRead(tables, list(body["texts"]))  # type: ignore[call-overload]
        return found

    return SimpleNamespace(
        NAME="liteparse",
        LABEL="LiteParse",
        installed=lambda: True,
        unsupported=lambda: None,
        convert=convert,
    )


def test_a_pdf_is_parsed_once_per_sha256(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setitem(fallback.ENGINES, "liteparse", fake_engine(calls))
    pdf = write_bank_pdf(tmp_path / "s.pdf")
    config = dataclasses.replace(load_config(root), pdf_parser="liteparse")
    run = Run("ingest", {}, root)

    masking = (company_db_path(root, "acme"), profiles_for("bank"))
    first, path = bank_pdf.read_parse(run, config, pdf, sha256_of(pdf), masking)
    second, _ = bank_pdf.read_parse(run, config, pdf, sha256_of(pdf), masking)

    assert calls == [1]
    assert first == second  # the saved tokens read back as the numbers, in memory
    assert path.startswith("cache/bank/")
    saved = (root / path).read_text(encoding="utf-8")
    assert json.loads(saved)["engine"] == "liteparse"
    assert "917020012345678" not in saved
    assert "Account Number : ACCT-0001" in saved


def test_llamaparse_reads_a_recorded_response_and_is_never_called(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llamaparse_run, "installed", lambda: True)
    monkeypatch.delenv(llamaparse_run.API_KEY_ENV, raising=False)

    def never(*_: Any) -> None:
        raise AssertionError("LlamaParse was called")

    monkeypatch.setattr(llamaparse_run, "_parse", never)
    pdf = write_bank_pdf(tmp_path / "s.pdf")
    recorded = root / "cache" / "llamaparse" / f"{sha256_of(pdf)}-p1-2.json"
    recorded.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "llamaparse_axis_response.json", recorded)

    lines = load(root, pdf, parser="llamaparse")

    assert "read by llamaparse" in lines[0]
    assert "ties" in lines[1]
    assert rows(root, "SELECT line_count, ties FROM bank_statements") == [(4, True)]


def test_llamaparse_without_a_key_raises_and_loads_nothing(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llamaparse_run, "installed", lambda: True)
    monkeypatch.delenv(llamaparse_run.API_KEY_ENV, raising=False)
    pdf = write_bank_pdf(tmp_path / "s.pdf")

    with pytest.raises(PdfAddonFailed, match="LLAMA_CLOUD_API_KEY"):
        load(root, pdf, parser="llamaparse")
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]
    assert not bank_pdf.saved_parse(root, sha256_of(pdf)).exists()
