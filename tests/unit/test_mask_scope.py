"""Masking scope (fin.diligence.mask, docs/decisions/masking.md): only the client's fields are
tokens (payroll names and codes, the starred register fields); bank text keeps names as filed in
either load order; `Parties` matches a name in the books or bank text to its register token."""

import dataclasses
from pathlib import Path

import pytest

from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, transaction, utcnow
from fin.db.schema import ensure_database
from fin.diligence.bank import ingest_bank
from fin.diligence.mask import Masker, Parties
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


def rows(root: Path, sql: str) -> list[tuple[object, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


@pytest.mark.parametrize("bank_first", [True, False])
def test_bank_text_keeps_names_and_payroll_stays_tokens_in_either_order(
    root: Path, tmp_path: Path, bank_first: bool
) -> None:
    if bank_first:
        bank(root, tmp_path)
        lines = payroll(root, tmp_path)
    else:
        payroll(root, tmp_path)
        lines = bank(root, tmp_path)

    remarks = [r for (r,) in rows(root, 'SELECT remarks FROM bank_lines ORDER BY "row"')]
    assert remarks == ["SALARY Meera Kulkarni", "Bank charges"]
    assert not any("earlier free-text" in line for line in lines)
    names = rows(root, "SELECT DISTINCT employee_name, employee_code FROM payroll_lines")
    assert names and all(n.startswith("EMP-") and c.startswith("ECODE-") for n, c in names)
    assert ("Meera Kulkarni",) not in rows(root, "SELECT employee_name FROM payroll_lines")


def test_parties_match_a_name_in_the_books_or_bank_text_to_its_register_token(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con, transaction(con):
        masker = Masker(con, "r1")
        masker.file_id = 1
        orchid = masker.token("customer", "Orchid Systems Pvt Ltd")
        orchid_labs = masker.token("customer", "Orchid Systems Pvt Ltd Labs")
        banyan = masker.token("vendor", "Banyan Traders")
        masker.token("employee_name", "Meera Kulkarni")
        parties = Parties(con)
        customers = Parties(con, ("customer",))

    assert parties.token("orchid  systems PVT ltd") == orchid
    assert parties.party("Banyan Traders") == banyan
    assert parties.party("Rent") == "Rent"  # not a register party: the name as stored
    assert parties.token("Meera Kulkarni") is None  # an employee is not a party
    assert parties.find("NEFT ORCHID SYSTEMS PVT LTD LABS INV-1") == orchid_labs  # longest wins
    assert parties.find("NEFT-Orchid Systems Pvt Ltd-INV101") == orchid
    assert parties.find("Orchid Systems Pvt Ltdx") is None  # whole words only
    assert customers.find("RTGS Banyan Traders") is None  # a vendor is not a payer
    assert parties.find(None) is None
