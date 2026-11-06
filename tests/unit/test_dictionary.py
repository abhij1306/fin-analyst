"""The seeded dictionary maps every synthetic fixture label to the answer key's account."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import find, load_label_map, map_rows
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


def add_company_mapping(root: Path, label: str, section: str, account: str) -> None:
    """A company-scope row as stored, key already normalized (by the code of its day)."""
    with connect(dictionary_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO label_map (label, statement, section, account, scope, company, source, "
            "created_at, run_id) VALUES (?, 'balance_sheet', ?, ?, 'company', 'acme', 'test', ?, "
            "'test')",
            [label, section, account, datetime(2026, 1, 1)],
        )


def test_company_mapping_for_any_section_wins_over_a_global_one_for_the_section(
    initialised: Path,
) -> None:
    add_company_mapping(initialised, "borrowings", "", "other_non_current_liabilities")
    label_map = load_label_map(initialised, "acme")

    mapping = find(label_map, "balance_sheet", "Current liabilities", "Borrowings")

    assert mapping is not None and mapping.account == "other_non_current_liabilities"


def test_a_key_stored_before_a_normalization_change_still_matches(initialised: Path) -> None:
    # Saved before leading roman numerals were dropped: "III Total assets" was "iii total assets".
    add_company_mapping(initialised, "iii total assets", "", "total_current_assets")

    mapping = find(load_label_map(initialised, "acme"), "balance_sheet", "", "III Total assets")

    assert mapping is not None and mapping.account == "total_current_assets"


def test_rows_under_a_sub_heading_map_by_the_part_of_the_balance_sheet_they_are_in(
    initialised: Path,
) -> None:
    rows = [
        ("Non-current assets", "Property, plant and equipment"),
        ("Financial Assets", "Loans"),
        ("Financial Assets", "Total non-current assets"),
        ("Financial assets", "Loans"),
        ("Equity", "Total equity"),
        ("Financial liabilities", "Borrowings"),
        ("Financial liabilities", "Total non-current liabilities"),
        ("Financial liabilities", "Borrowings"),
        ("Trade payables", "Provisions"),
    ]

    mappings = map_rows(
        load_label_map(initialised, "acme"),
        [("balance_sheet", "standalone", section, label) for section, label in rows],
    )

    assert [m and m.account for m in mappings] == [
        "ppe_net",
        "loans_non_current",
        "total_non_current_assets",
        "loans_current",
        "total_equity",
        "lt_borrowings",
        "total_non_current_liabilities",
        "st_borrowings",
        "st_provisions",
    ]


def test_the_part_starts_again_for_each_statement_and_scope(initialised: Path) -> None:
    rows = [
        ("balance_sheet", "standalone", "Equity", "Total equity"),
        ("balance_sheet", "consolidated", "Financial liabilities", "Borrowings"),
    ]

    mappings = map_rows(load_label_map(initialised, "acme"), rows)

    assert [m and m.account for m in mappings] == ["total_equity", None]


def test_eps_accounts_are_per_share_and_money_accounts_are_not(initialised: Path) -> None:
    label_map = load_label_map(initialised, "acme")

    basic = find(label_map, "profit_and_loss", "Earnings per equity share", "(a) Basic (in Rs.)")
    revenue = find(label_map, "profit_and_loss", "", "Revenue from operations")

    assert (basic and (basic.account, basic.per_share)) == ("eps_basic", True)
    assert (revenue and (revenue.account, revenue.per_share)) == ("revenue", False)
