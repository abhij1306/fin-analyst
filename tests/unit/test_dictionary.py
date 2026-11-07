"""The seeded dictionary maps every synthetic fixture label to the answer key's account."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import LabelMap, find, load_label_map, map_rows
from fin.paths import dictionary_db_path

BORROWINGS = "(a) Financial liabilities - Borrowings"  # under both non-current and current


@pytest.fixture
def initialised(data_root: Path) -> Path:
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    return data_root


@pytest.fixture(scope="module")
def seed_map(tmp_path_factory: pytest.TempPathFactory) -> LabelMap:
    """The global label map of a freshly initialised data folder, built once for the module."""
    root = tmp_path_factory.mktemp("seed") / "FinAnalysis"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("FIN_DATA_DIR", str(root))
        assert CliRunner().invoke(app, ["init"]).exit_code == 0
    return load_label_map(root.resolve(), "acme")


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
    assert find(label_map, "balance_sheet", "", "Capital reserve on amalgamation") is None


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


@pytest.mark.parametrize(
    ("statement", "section", "label", "account"),
    [
        # Schedule III Division I (old format) and Division II
        (
            "balance_sheet",
            "",
            "Share application money pending allotment",
            "share_application_money",
        ),
        ("balance_sheet", "Non-current assets", "(a) Fixed assets", "fixed_assets"),
        (
            "balance_sheet",
            "",
            "a) Property,Plant & Equipment and Intangible Assets",
            "fixed_assets",
        ),
        ("balance_sheet", "", "(b) Investment property", "investment_property"),
        # Often printed after a "Current assets" subtotal that the seed reads as the total: review
        ("balance_sheet", "", "Assets held-for-Sale (Refer Note 8)", None),
        (
            "balance_sheet",
            "Non-current assets",
            "Trade receivables",
            "trade_receivables_non_current",
        ),
        ("balance_sheet", "Current assets", "Trade receivables", "trade_receivables"),
        ("balance_sheet", "", "(i) Borrowings 13 (a)", None),  # current or non-current: by part
        ("balance_sheet", "Current liabilities", "(i) Borrowings 13 (a)", "st_borrowings"),
        (
            "balance_sheet",
            "Non-current liabilities",
            "Employee benefit obligations",
            "lt_provisions",
        ),
        # Division III (NBFCs)
        ("balance_sheet", "Financial liabilities", "(c) Debt securities", "debt_securities"),
        (
            "balance_sheet",
            "Financial liabilities",
            "(d) Borrowings (other than debt securities)",
            "borrowings",
        ),
        ("balance_sheet", "Financial liabilities", "(e) Deposits", "deposits"),
        ("balance_sheet", "", "(f) Subordinated liabilities", "subordinated_liabilities"),
        ("balance_sheet", "Receivables", "(I) Trade receivables", "trade_receivables"),
        ("balance_sheet", "", "(k) Other non-financial assets", "other_non_financial_assets"),
        ("balance_sheet", "", "Total financial assets", "total_financial_assets"),
        ("balance_sheet", "Financial assets", "Loans", None),  # Division II uses it too
        ("profit_and_loss", "Revenue from operations", "(i) Interest income", "interest_income"),
        ("profit_and_loss", "", "(ii) Fees and commission income", "fee_and_commission_income"),
        ("profit_and_loss", "", "(I) Total revenue from operations", "revenue"),
        ("profit_and_loss", "", "Net loss on fair value changes", "net_loss_fair_value_changes"),
        (
            "profit_and_loss",
            "Expenses",
            "(iv) Impairment on financial instruments",
            "impairment_on_financial_instruments",
        ),
        # Banks (Third Schedule, Forms A and B)
        ("balance_sheet", "CAPITAL AND LIABILITIES", "Capital", "share_capital"),
        ("balance_sheet", "CAPITAL AND LIABILITIES", "Deposits", "deposits"),
        ("balance_sheet", "CAPITAL AND LIABILITIES", "Total", "total_equity_and_liabilities"),
        (
            "balance_sheet",
            "ASSETS",
            "Cash and balances with Reserve Bank of India",
            "balances_with_rbi",
        ),
        ("balance_sheet", "ASSETS", "Advances", "loans"),
        ("balance_sheet", "ASSETS", "Total", "total_assets"),
        ("balance_sheet", "", "Contingent liabilities", "contingent_liabilities"),
        ("profit_and_loss", "I. INCOME", "Interest earned", "revenue"),
        ("profit_and_loss", "II. EXPENDITURE", "Interest expended", "interest_expended"),
        ("profit_and_loss", "II. EXPENDITURE", "Total", "total_expenses"),
        (
            "profit_and_loss",
            "IV. APPROPRIATIONS",
            "Transfer to statutory reserve",
            "appropriations",
        ),
        # SEBI results format
        (
            "profit_and_loss",
            "",
            "Paid-up equity share capital (Face Value of Rs.10 /- per share)",
            "paid_up_share_capital",
        ),
        ("profit_and_loss", "", "11. Other equity", "reserves_memo"),
        ("profit_and_loss", "", "Adjustment of tax relating to earlier periods", "tax_expense"),
        ("profit_and_loss", "", "Total B", None),
        ("profit_and_loss", "Expenses", "Total B", "total_expenses"),
        # Cash flow (Ind AS 7 / AS 3)
        ("cash_flow", "", "Profit for the year", "cf_pat"),
        (
            "cash_flow",
            "",
            "Net loss/(gain)on disposal of property, plant and equipment",
            "cf_gain_on_sale_of_ppe",
        ),
        ("cash_flow", "", "Unrealised exchange loss/(gain)", "cf_unrealised_fx"),
        ("cash_flow", "", "Increase / (decrease) in long term provisions", "cf_change_provisions"),
        (
            "cash_flow",
            "",
            "Bank balances not considered as cash and cash equivalents",
            "cf_bank_deposits_net",
        ),
        ("cash_flow", "", "Payment of lease liabilities", "cf_lease_payments"),
        (
            "cash_flow",
            "C. Cash flow from financing activities",
            "Finance costs",
            "cf_finance_cost_paid",
        ),
        ("cash_flow", "Adjustments for:", "Finance costs", "cf_add_finance_cost"),
        (
            "cash_flow",
            "",
            "Effect of exchange rate changes on cash and cash equivalents",
            "cf_fx_effect",
        ),
    ],
)
def test_standard_format_labels_map_to_their_account(
    seed_map: LabelMap, statement: str, section: str, label: str, account: str | None
) -> None:
    mapping = find(seed_map, statement, section, label)

    assert (mapping and mapping.account) == account


def test_the_part_is_kept_when_standalone_and_consolidated_rows_alternate(
    seed_map: LabelMap,
) -> None:
    rows = [
        (scope, section, label)
        for section, label in [
            ("Non-current assets", "Property, plant and equipment"),
            ("Financial assets", "Investments"),
            ("Financial assets", "Total non-current assets"),
            ("Financial assets", "Loans"),
        ]
        for scope in ("standalone", "consolidated")
    ]

    mappings = map_rows(
        seed_map, [("balance_sheet", scope, section, label) for scope, section, label in rows]
    )

    assert [m and m.account for m in mappings] == [
        "ppe_net",
        "ppe_net",
        "non_current_investments",
        "non_current_investments",
        "total_non_current_assets",
        "total_non_current_assets",
        "loans_current",
        "loans_current",
    ]
