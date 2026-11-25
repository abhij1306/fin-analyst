"""Masking does not depend on load order (fin.diligence.mask): a name learned from a later file is
masked in the free text already stored, and an employee code is never searched for in free text."""

import dataclasses
from pathlib import Path

import pytest

from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, transaction, utcnow
from fin.db.schema import ensure_database
from fin.diligence.bank import ingest_bank
from fin.diligence.mask import Masker
from fin.diligence.sources import ingest_source
from fin.paths import company_db_path
from tests.bank_fixtures import write_statement
from tests.payroll_fixtures import write_payroll

SALARY: list[tuple[int, str, int, int]] = [
    (3, "SALARY Meera Kulkarni", 90000, 0),
    (10, "Bank charges", 590, 0),
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
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
    return root


def bank(root: Path, tmp_path: Path) -> list[str]:
    config = dataclasses.replace(load_config(root), pdf_parser="none")
    path = write_statement(tmp_path / "s.xlsx", year=2024, rows=SALARY)
    return ingest_bank(Run("ingest", {}, root), config, "acme", path, None).lines


def payroll(root: Path, tmp_path: Path) -> list[str]:
    path = write_payroll(tmp_path / "payroll.xlsx")
    run = Run("ingest", {}, root)
    return ingest_source(run, load_config(root), "acme", path, "payroll", None).lines


def remarks(root: Path) -> list[str]:
    with connect(company_db_path(root, "acme")) as con:
        rows = con.execute('SELECT remarks FROM bank_lines ORDER BY "row"').fetchall()
    return [r[0] for r in rows]


def test_a_name_learned_after_the_bank_statement_is_masked_in_its_rows(
    root: Path, tmp_path: Path
) -> None:
    bank(root, tmp_path)
    assert "Meera Kulkarni" in remarks(root)[0]  # not yet a known name

    lines = payroll(root, tmp_path)

    assert remarks(root) == ["SALARY EMP-0001", "Bank charges"]
    assert any("1 earlier free-text values masked" in line for line in lines)


def test_the_other_order_gives_the_same_rows(root: Path, tmp_path: Path) -> None:
    payroll(root, tmp_path)
    lines = bank(root, tmp_path)

    assert remarks(root) == ["SALARY EMP-0001", "Bank charges"]
    assert not any("earlier free-text" in line for line in lines)


def test_an_employee_code_is_known_but_never_found_in_free_text(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con, transaction(con):
        masker = Masker(con, "r1")
        masker.file_id = 1
        code = masker.token("employee_code", "101")
        masker.token("employee_code", "7")
        name = masker.token("employee_name", "Meera Kulkarni")

        assert masker.text("Rs 101.00 on 7 Mar") == "Rs 101.00 on 7 Mar"
        assert masker.text("101") == "101"
        assert masker.known("101") == code
        assert masker.text("pay Meera Kulkarni 101") == f"pay {name} 101"
