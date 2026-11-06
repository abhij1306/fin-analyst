"""The seeded dictionary maps every synthetic fixture label to the answer key's account."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import find, load_label_map
from fin.paths import dictionary_db_path

BORROWINGS = "(a) Financial liabilities - Borrowings"  # under both non-current and current


@pytest.fixture
def initialised(data_root: Path) -> Path:
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    return data_root


def statements(root: Path) -> dict[str, str]:
    with connect(dictionary_db_path(root)) as con:
        return dict(con.execute("SELECT account, statement FROM accounts").fetchall())


@pytest.mark.parametrize("company", ["kestrel", "bluepeak", "tarang"])
def test_answer_key_labels_map_through_the_dictionary(
    initialised: Path, answer_key: dict[str, Any], company: str
) -> None:
    statement_of = statements(initialised)
    label_map = load_label_map(initialised, company)
    labels = answer_key["companies"][company]["labels"]
    expected = {label: account for label, account in labels.items() if label != BORROWINGS}
    mapped = {}
    for label, account in expected.items():
        mapping = find(label_map, statement_of[account], "", label)
        mapped[label] = None if mapping is None else mapping.account
    assert mapped == expected


def test_every_answer_key_account_is_in_the_dictionary(
    initialised: Path, answer_key: dict[str, Any]
) -> None:
    statement_of = statements(initialised)
    for company in answer_key["companies"].values():
        for years in company["facts"].values():
            for accounts in years.values():
                assert set(accounts) <= set(statement_of)


def test_section_tells_apart_the_same_label(initialised: Path) -> None:
    label_map = load_label_map(initialised, "kestrel")

    def account(section: str) -> str | None:
        mapping = find(label_map, "balance_sheet", section, BORROWINGS)
        return None if mapping is None else mapping.account

    assert account("(2) Non-current liabilities") == "lt_borrowings"
    assert account("(3) Current liabilities") == "st_borrowings"
    assert account("") is None


def test_statement_tells_apart_the_same_label(initialised: Path) -> None:
    label_map = load_label_map(initialised, "kestrel")
    pl = find(label_map, "profit_and_loss", "", "Profit before tax")
    cf = find(label_map, "cash_flow", "", "Profit before tax")
    assert (pl and pl.account, cf and cf.account) == ("pbt", "cf_pbt")


def test_unknown_label_is_not_mapped(initialised: Path) -> None:
    label_map = load_label_map(initialised, "kestrel")
    assert find(label_map, "balance_sheet", "", "Goodwill on consolidation") is None


def test_company_mapping_wins_for_that_company_only(initialised: Path) -> None:
    with connect(dictionary_db_path(initialised), write=True) as con:
        con.execute(
            "INSERT INTO label_map (label, statement, section, account, scope, company, source, "
            "created_at, run_id) VALUES ('sundry debtors', 'balance_sheet', '', "
            "'other_current_assets', 'company', 'acme', 'test', ?, 'test')",
            [datetime(2026, 1, 1)],
        )
    acme = find(load_label_map(initialised, "acme"), "balance_sheet", "", "Sundry debtors")
    other = find(load_label_map(initialised, "bluepeak"), "balance_sheet", "", "Sundry debtors")
    assert (acme and acme.account, other and other.account) == (
        "other_current_assets",
        "trade_receivables",
    )
