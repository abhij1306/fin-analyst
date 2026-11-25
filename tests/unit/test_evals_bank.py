"""Golden evaluation of bank statements (kind bank_closing): a closing balance a person read on the
statement passes, a planted wrong one fails, and `fin eval init` lists each statement."""

import csv
import dataclasses
from pathlib import Path

import pytest

from fin.audit.logger import Run
from fin.config import load_config
from fin.diligence.bank import ingest_bank
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from tests.bank_fixtures import CLOSING, write_statement
from tests.engagement_root import engagement_root

ACCOUNT = "123456789012"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = engagement_root(tmp_path)
    config = dataclasses.replace(load_config(root), pdf_parser="none")
    ingest_bank(Run("t", {}, root), config, "acme", write_statement(tmp_path / "s.xlsx"), None)
    return root


def golden(root: Path, expected: str, account: str = ACCOUNT) -> None:
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = ("bank_closing", "2024-04-30", "", "", account, expected, "rupees", "s.xlsx", "yes")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerow(line)


def test_a_closing_balance_checked_against_the_statement_passes(root: Path) -> None:
    golden(root, f"{CLOSING:,}.00")

    result = run_eval(Run("t", {}, root), "acme")

    assert [c["outcome"] for c in result["checks"]] == ["pass"]
    assert result["counts"]["bank_statements"] == {"excel, ties": 1}


def test_a_wrong_closing_balance_fails_and_an_unknown_account_is_missing(root: Path) -> None:
    golden(root, f"{CLOSING + 1:,}.00")
    [check] = run_eval(Run("t", {}, root), "acme")["checks"]
    assert (check["outcome"], check["fin"]) == ("fail", CLOSING)

    golden(root, f"{CLOSING:,}.00", account="555")
    [check] = run_eval(Run("t", {}, root), "acme")["checks"]
    assert check["outcome"] == "missing"


def test_init_lists_each_bank_statement_with_its_source(root: Path) -> None:
    result = init_golden(Run("t", {}, root), "acme")
    [row] = list(csv.DictReader(result["path"].open(encoding="utf-8")))
    assert (row["kind"], row["period"], row["ledger"], row["expected"], row["checked"]) == (
        "bank_closing",
        "2024-04-30",
        ACCOUNT,
        "",
        "",
    )
    assert row["source"] == "s.xlsx closing balance"
