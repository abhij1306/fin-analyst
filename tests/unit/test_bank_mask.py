"""Bank account numbers are tokens (SPEC-17 item 1; fin.diligence.bank_mask): after loading
synthetic Excel and PDF statements no table but `mask_values`, no output line, log line or saved
parse holds an account number; two statements of one account carry one token and still chain; a
known number in bank text is masked whichever statement loads first; an account number is its own
kind."""

import dataclasses
from pathlib import Path
from typing import Any

import duckdb
import pytest

from fin.audit.errors import SourcePeriodInvalid
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, transaction, utcnow
from fin.db.schema import ensure_database
from fin.diligence.bank import ingest_bank
from fin.diligence.bank_mask import mask_text, numbers_in
from fin.diligence.mask import ACCOUNT, Masker
from fin.extract import fallback
from fin.extract.profiles import profiles_for
from fin.ingest import sha256_of
from fin.paths import company_db_path
from tests.bank_fixtures import CLOSING, pdf_pages, write_bank_pdf, write_statement
from tests.unit.test_bank import fake_engine

FIRST, SECOND, PDF_ACCOUNT = "123456789012", "998877665544", "917020012345678"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    (root / "logs").mkdir(exist_ok=True)
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


def load(root: Path, path: Path, parser: str = "none") -> list[str]:
    config = dataclasses.replace(load_config(root), pdf_parser=parser)
    return ingest_bank(Run("ingest", {}, root), config, "acme", path, None).lines


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def stored_text(db: Path, skip: set[str]) -> str:
    """Every value of every table of a database (but `skip`), as one text."""
    with duckdb.connect(str(db), read_only=True) as con:
        tables = [
            t
            for (t,) in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
            ).fetchall()
            if t not in skip
        ]
        return " ".join(
            str(row) for t in tables for row in con.execute(f'SELECT * FROM "{t}"').fetchall()
        )


def files_text(root: Path) -> str:
    """Every file of the data folder but the archived originals and the databases."""
    return " ".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in root.rglob("*")
        if p.is_file() and "archive" not in p.parts and p.suffix not in (".duckdb", ".wal")
    )


def test_no_table_output_log_or_saved_parse_holds_an_account_number(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(fallback.ENGINES, "liteparse", fake_engine([]))
    out = load(root, write_statement(tmp_path / "apr.xlsx"))
    out += load(root, write_statement(tmp_path / "may.xlsx", month=5, last_day=31, opening=CLOSING))
    transfer = [(6, f"TRF TO {FIRST} OWN ACCOUNT", 1000, 0), (9, "Bank charges", 10, 0)]
    out += load(root, write_statement(tmp_path / "b.xlsx", "sbi", account=SECOND, rows=transfer))
    out += load(root, write_bank_pdf(tmp_path / "s.pdf"), parser="liteparse")

    text = " ".join(out) + stored_text(company_db_path(root, "acme"), {"mask_values"})
    text += files_text(root)
    for number in (FIRST, SECOND, PDF_ACCOUNT):
        assert number not in text
    assert (root / "cache" / "bank").is_dir()
    assert rows(root, "SELECT kind, count(*) FROM mask_values GROUP BY kind") == [(ACCOUNT, 3)]
    assert rows(root, "SELECT remarks FROM bank_lines WHERE remarks LIKE 'TRF%'") == [
        ("TRF TO ACCT-0001 OWN ACCOUNT",)
    ]


def test_two_statements_of_one_account_carry_one_token_and_still_chain(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_statement(tmp_path / "apr.xlsx"))
    load(root, write_statement(tmp_path / "may.xlsx", month=5, last_day=31, opening=CLOSING + 7))
    load(root, write_statement(tmp_path / "jun.xlsx", "sbi", month=6, account=SECOND))

    assert rows(root, "SELECT DISTINCT account FROM bank_statements ORDER BY 1") == [
        ("ACCT-0001",),
        ("ACCT-0002",),
    ]
    assert rows(root, "SELECT DISTINCT account FROM bank_lines ORDER BY 1") == [
        ("ACCT-0001",),
        ("ACCT-0002",),
    ]
    # April and May still join: May's opening is checked against April's closing
    assert rows(root, "SELECT check_name, file_id, label FROM findings") == [
        ("bank_balance_gap", 2, "ICICI ACCT-0001 INR")
    ]


def test_a_number_in_bank_text_is_masked_whichever_statement_loads_first(
    root: Path, tmp_path: Path
) -> None:
    transfer = [(6, f"NEFT/{SECOND}/SWEEP", 500, 0)]
    load(root, write_statement(tmp_path / "a.xlsx", rows=transfer, opening=1000))
    assert rows(root, "SELECT remarks FROM bank_lines") == [(f"NEFT/{SECOND}/SWEEP",)]

    load(root, write_statement(tmp_path / "b.xlsx", "sbi", account=SECOND))

    assert rows(root, "SELECT remarks FROM bank_lines WHERE file_id = 1") == [
        ("NEFT/ACCT-0002/SWEEP",)
    ]


def test_a_saved_parse_holds_tokens_and_outlives_a_failed_load(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setitem(fallback.ENGINES, "liteparse", fake_engine(calls))
    pdf = write_bank_pdf(tmp_path / "s.pdf")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET periods = 'FY24'")
    with pytest.raises(SourcePeriodInvalid):
        load(root, pdf, parser="liteparse")
    saved = (root / "cache" / "bank" / f"{sha256_of(pdf)}.json").read_text(encoding="utf-8")
    assert PDF_ACCOUNT not in saved
    assert "ACCT-0001" in saved

    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET periods = 'FY25'")
    lines = load(root, pdf, parser="liteparse")

    assert calls == [1]  # read from the saved parse, the tokens back as the number in memory
    assert "Axis ACCT-0001 INR" in lines[1]
    assert rows(root, "SELECT first_file_id, account FROM mask_values, bank_statements") == [
        (None, "ACCT-0001")
    ]


def test_an_account_number_is_its_own_kind(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con, transaction(con):
        masker = Masker(con, "r1")
        masker.file_id = 1
        code = masker.token("employee_code", "100200300")
        account = masker.token(ACCOUNT, "100200300")
        assert masker.token("employee_code", "100200300") == code
        assert masker.known("100200300") == code
        assert masker.accounts() == {"100200300": account}
    assert code.startswith("ECODE-")
    assert account == "ACCT-0001"


def test_numbers_are_found_by_the_profiles_and_masked_as_whole_numbers() -> None:
    assert numbers_in(pdf_pages(), profiles_for("bank")) == {PDF_ACCOUNT}
    tokens = {FIRST: "ACCT-0001"}
    assert mask_text(f"TRF {FIRST}/x", tokens) == "TRF ACCT-0001/x"
    assert mask_text(f"TRF 9{FIRST}", tokens) == f"TRF 9{FIRST}"  # part of a longer number
