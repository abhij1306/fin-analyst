"""Ledger mapping (fin.diligence.ledgers), tb_vs_statements (fin.diligence.recon), the stub and
G1's trial balance checks (fin.diligence.tb_checks) on a synthetic Tally trial balance: a planted
account difference at or above the threshold becomes a draft query, one below it is listed, and a
stub loads labelled unaudited."""

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import InvalidMapping, ReconNotPossible, SourcePeriodInvalid
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import gates, ledgers, recon
from fin.diligence.engagement import open_engagement
from fin.diligence.sources import ingest_source
from fin.diligence.stub import parse_stub
from fin.map.dictionary import load_accounts, load_seed
from fin.paths import company_db_path, dictionary_db_path
from tests.tally_fixtures import BANK, VOUCHERS, write_ledger_vouchers, write_trial_balance

ACCOUNTS = {
    "Share Capital": "share_capital",
    "VEND-0001": "trade_payables",
    "VEND-0002": "trade_payables",
    "GST Payable": "other_current_liabilities",
    "CUST-0001": "trade_receivables",
    "CUST-0002": "trade_receivables",
    BANK: "cash",
    "Software Sales": "revenue",
    "Rent": "other_expenses",
    "Salaries": "employee_expense",
}
# The audited FY25 standalone statement: revenue is 1,00,000 above the trial balance (at the
# threshold: queried), other expenses 30,000 above it (below: listed). total_assets has lines
# below it, so it is not a detail line. other_equity is the year's loss from the P&L ledgers.
FIGURES = [
    ("balance_sheet", "share_capital", 500000),
    ("balance_sheet", "trade_payables", 180000),
    ("balance_sheet", "other_current_liabilities", 40000),
    ("balance_sheet", "trade_receivables", 300000),
    ("balance_sheet", "cash", 250000),
    ("balance_sheet", "total_assets", 550000),
    ("balance_sheet", "other_equity", -170000),
    ("profit_and_loss", "revenue", 1000000),
    ("profit_and_loss", "employee_expense", 950000),
    ("profit_and_loss", "other_expenses", 150000),
]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with the dictionary, company acme, its audited FY25 statement figures and an
    open engagement for FY25 with the stub FY26-YTD-SEP, made once: each test copies it."""
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at) VALUES (1, 'fs.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'x', 'r0', now())"
        )
        for raw_id, (statement, account, value) in enumerate(FIGURES, start=1):
            con.execute(
                "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                "VALUES (?, 1, 'BS', ?, ?, '', ?, '', '', 1, 'r0')",
                [raw_id, f"C{raw_id}", statement, account],
            )
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
                [raw_id, account, value],
            )
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, "
            "status, opened_run_id, opened_at) VALUES ('FY25', 100000, 'FY26-YTD-SEP', "
            "DATE '2025-04-01', DATE '2025-09-30', 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def run(root: Path, command: str = "test") -> Run:
    return Run(command, {}, root)


def load(root: Path, path: Path, kind: str) -> list[str]:
    return ingest_source(run(root), load_config(root), "acme", path, kind, None).lines


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def map_all(root: Path) -> None:
    proposals = [
        {"ledger": ledger, "account": account, "confidence": 0.9, "reason": "by name"}
        for ledger, account in ACCOUNTS.items()
    ]
    assert ledgers.propose(run(root), "acme", proposals) == len(ACCOUNTS)
    ids = [row["ledger_id"] for row in ledgers.list_ledgers(run(root), "acme")]
    ledgers.confirm(run(root), "acme", ids)


def g1_lines(root: Path) -> list[tuple[bool, str]]:
    with connect(company_db_path(root, "acme")) as con:
        [g1, *_] = gates.evaluate(gates.context(con, root, "acme"))
    return [(line["ok"], line["text"]) for line in g1["checklist"]][3:]


def test_the_stub_is_the_year_to_date_after_the_last_period() -> None:
    stub = parse_stub("fy27-ytd-sep", ["FY25", "FY26"], 4)
    assert (stub.name, stub.start.isoformat(), stub.end.isoformat()) == (
        "FY27-YTD-SEP",
        "2026-04-01",
        "2026-09-30",
    )
    assert parse_stub("FY27-YTD-JAN", ["FY26"], 4).end.isoformat() == "2027-01-31"
    for text, problem in (
        ("FY28-YTD-SEP", "right after the last annual period FY26"),
        ("FY27-YTD-MAR", "that is the annual period FY27"),
        ("FY27-SEP", "not a stub period"),
    ):
        with pytest.raises(ValueError, match=problem):
            parse_stub(text, ["FY26"], 4)


def test_open_with_a_stub_and_a_bad_stub_is_e808(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET status = 'closed'")
    run_root = run(root)
    with pytest.raises(SourcePeriodInvalid, match="FY26"):
        open_engagement(run_root, "acme", ["FY24", "FY25"], None, "FY27-YTD-SEP")

    row = open_engagement(run(root), "acme", ["FY24", "FY25"], None, "FY26-YTD-SEP")

    assert (row["stub"], str(row["stub_start"]), str(row["stub_end"])) == (
        "FY26-YTD-SEP",
        "2025-04-01",
        "2025-09-30",
    )


def test_ledgers_are_proposed_then_confirmed_only_by_review(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    assert lines[-1] == "10 new ledgers waiting for an account (`fin map ledgers acme`)"
    run_root = run(root)
    with pytest.raises(InvalidMapping, match="unknown account 'sales'"):
        ledgers.propose(
            run_root,
            "acme",
            [{"ledger": "Rent", "account": "sales", "confidence": 1, "reason": ""}],
        )
    run_root = run(root)
    with pytest.raises(InvalidMapping, match="cash_flow account"):
        ledgers.propose(
            run_root,
            "acme",
            [{"ledger": "Rent", "account": "cfo", "confidence": 1, "reason": ""}],
        )

    proposals = [{"ledger": "Rent", "account": "other_expenses", "confidence": 0.9, "reason": "r"}]
    ledgers.propose(run(root), "acme", proposals)

    assert rows(root, "SELECT status, count(*) FROM ledger_accounts GROUP BY ALL ORDER BY 1") == [
        ("pending", 9),
        ("proposed", 1),
    ]
    [rent] = [r for r in ledgers.list_ledgers(run(root), "acme", True) if r["ledger"] == "Rent"]
    assert ledgers.format_ledger(rent) == (
        f"ledger #{rent['ledger_id']} [proposed] 'Rent' in Indirect Expenses (trial balance) "
        "-> other_expenses (confidence 0.9): r"
    )
    run_root = run(root)
    with pytest.raises(InvalidMapping, match="has no proposed account"):
        ledgers.confirm(run_root, "acme", [rent["ledger_id"], rent["ledger_id"] - 1])
    [confirmed] = ledgers.confirm(run(root), "acme", [rent["ledger_id"]])
    assert (confirmed["status"], confirmed["account"], confirmed["side"]) == (
        "confirmed",
        "other_expenses",
        1,
    )
    run_root = run(root)
    with pytest.raises(InvalidMapping, match="only the user changes it"):
        ledgers.propose(run_root, "acme", proposals)


def test_account_sides_follow_the_dictionary_tree(root: Path) -> None:
    accounts = load_accounts(root)
    sides = {name: ledgers.side(accounts, name) for name in set(ACCOUNTS.values())}
    assert sides == {
        "share_capital": -1,
        "trade_payables": -1,
        "other_current_liabilities": -1,
        "trade_receivables": 1,
        "cash": 1,
        "revenue": -1,
        "other_expenses": 1,
        "employee_expense": 1,
    }


def test_tb_vs_statements_queries_a_difference_at_the_threshold_and_lists_one_below(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")

    [unmapped] = recon.tb_vs_statements(run(root), "acme")
    assert len(unmapped["unmapped"]) == 10
    assert unmapped["drafted"] == []
    assert rows(root, "SELECT count(*) FROM accountant_queries") == [(0,)]

    map_all(root)
    [result] = recon.tb_vs_statements(run(root), "acme", "FY25")

    lines = {line["account"]: line for line in result["lines"]}
    assert {a: (line["kind"], line["difference"]) for a, line in lines.items()} == {
        "share_capital": ("matched", 0),
        "other_equity": ("matched", 0),
        "trade_payables": ("matched", 0),
        "other_current_liabilities": ("matched", 0),
        "trade_receivables": ("matched", 0),
        "cash": ("matched", 0),
        "revenue": ("difference", Decimal("-100000.00")),
        "employee_expense": ("matched", 0),
        "other_expenses": ("difference", Decimal("-30000.00")),
    }
    assert lines["other_expenses"]["query_id"] is None
    assert lines["trade_payables"]["left_source"] == "tb.xlsx Trial Balance!10, 11"
    assert lines["other_equity"]["left_source"] == (
        "the year's profit from 3 profit and loss ledgers"
    )
    queries = rows(
        root,
        "SELECT query_id, origin, status, period, scope, amount, sources, question "
        "FROM accountant_queries",
    )
    assert queries == [
        (
            lines["revenue"]["query_id"],
            "recon",
            "draft",
            "FY25",
            "standalone",
            Decimal("-100000.00"),
            "tb.xlsx Trial Balance!21; fs.xlsx BS!C8",
            "FY25: the trial balance ledgers mapped to revenue total ₹900,000.00; the audited "
            "standalone statement shows ₹1,000,000.00 (difference -₹100,000.00). Please explain "
            "the difference.",
        )
    ]
    text = recon.format_result(result)
    assert "9 accounts, 7 matched, 1 at or above the threshold, 1 below it (listed)" in text
    assert (
        "difference other_expenses: trial balance ₹120,000.00, statements ₹150,000.00, "
        "difference -₹30,000.00 (below the threshold)" in text
    )

    [again] = recon.tb_vs_statements(run(root), "acme")
    assert (again["drafted"], again["kept"]) == ([], [lines["revenue"]["query_id"]])
    assert rows(root, "SELECT count(*) FROM accountant_queries") == [(1,)]

    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE accountant_queries SET status = 'withdrawn'")
    [after] = recon.tb_vs_statements(run(root), "acme")
    assert after["kept"] == []
    assert len(after["drafted"]) == 1


def test_a_draft_whose_difference_is_gone_is_listed_stale_and_never_withdrawn(
    root: Path, tmp_path: Path
) -> None:
    # Test run 2026-11-25: a ledger mapped to the wrong account drafted queries; once the mapping
    # was fixed the run no longer had their lines, and nothing said so.
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    map_all(root)
    [first] = recon.tb_vs_statements(run(root), "acme")
    [query_id] = first["drafted"]
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE facts SET value = 900000 WHERE account = 'revenue'")

    [again] = recon.tb_vs_statements(run(root), "acme")

    assert (again["drafted"], again["kept"], again["stale"]) == ([], [], [query_id])
    assert f"stale draft queries: #{query_id}" in recon.format_result(again)
    assert rows(root, "SELECT status FROM accountant_queries") == [("draft",)]


def test_no_draft_is_called_stale_while_a_ledger_is_unmapped(root: Path, tmp_path: Path) -> None:
    # a run with an unmapped ledger checks no line against earlier queries, so it cannot tell a
    # draft whose difference is gone from one whose difference is unchanged
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    map_all(root)
    [first] = recon.tb_vs_statements(run(root), "acme")
    [query_id] = first["drafted"]
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "UPDATE ledger_accounts SET status = 'pending', account = NULL, statement = NULL, "
            "side = NULL WHERE ledger = 'GST Payable'"
        )

    [again] = recon.tb_vs_statements(run(root), "acme")

    assert again["unmapped"] == ["GST Payable"] and again["stale"] == []
    assert "stale draft" not in recon.format_result(again)
    assert rows(root, "SELECT query_id, status FROM accountant_queries") == [(query_id, "draft")]


def test_g1_checks_the_trial_balance_and_reopens_when_it_changes(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    assert g1_lines(root) == [
        (False, "FY25: no loaded Ledger Vouchers (`fin ingest acme <file> --kind "
         "trial_balance|ledger_vouchers`)"),
        (False, "FY26-YTD-SEP: no loaded trial balance or Ledger Vouchers (`fin ingest acme "
         "<file> --kind trial_balance|ledger_vouchers`)"),
        (False, "10 trial balance ledgers not mapped to an account (`fin map ledgers acme`, "
         "then the user confirms them): Axis Bank Current Account, CUST-0001, CUST-0002, "
         "GST Payable, Rent and 5 more"),
        (False, "FY25: tb_vs_statements has not run (`fin reconcile acme --period FY25`)"),
        (True, "FY26-YTD-SEP: unaudited management accounts; checked against its Ledger "
         "Vouchers only (no audited statement)"),
    ]  # fmt: skip

    load(root, write_ledger_vouchers(tmp_path / "lv.xlsx", list(VOUCHERS)[:-1]), "ledger_vouchers")
    map_all(root)
    recon.tb_vs_statements(run(root), "acme")

    lines = g1_lines(root)
    assert lines[0] == (
        True,
        "FY25: trial balance and Ledger Vouchers loaded, every ledger in both",
    )
    assert lines[2:4] == [
        (True, "every trial balance ledger is mapped to an account"),
        (True, "FY25: trial balance reconciled to the statements; every difference at or above "
         "the threshold is in a query"),
    ]  # fmt: skip

    ids = [r["ledger_id"] for r in ledgers.list_ledgers(run(root), "acme") if r["ledger"] == "Rent"]
    ledgers.confirm(run(root), "acme", ids, "power_and_fuel")

    assert g1_lines(root)[3] == (
        False,
        "FY25: tb_vs_statements ran on data that has changed since (`fin reconcile acme "
        "--period FY25`)",
    )


def test_a_stub_loads_labelled_unaudited_and_is_not_reconciled(root: Path, tmp_path: Path) -> None:
    stub_period = "1-Apr-2025 to 30-Sep-2025"
    lines = load(
        root, write_trial_balance(tmp_path / "s.xlsx", period=stub_period), "trial_balance"
    )

    assert lines[0].endswith("; FY26-YTD-SEP is the stub: unaudited management accounts")
    assert rows(root, "SELECT period, basis FROM source_files") == [("FY26-YTD-SEP", "unaudited")]
    map_all(root)
    figures = rows(
        root,
        "SELECT period, basis, account, value FROM stub_figures WHERE account IN "
        "('revenue', 'cash', 'trade_payables') ORDER BY account",
    )
    assert figures == [
        ("FY26-YTD-SEP", "unaudited management accounts", "cash", Decimal("250000.00")),
        ("FY26-YTD-SEP", "unaudited management accounts", "revenue", Decimal("900000.00")),
        ("FY26-YTD-SEP", "unaudited management accounts", "trade_payables", Decimal("180000.00")),
    ]
    run_root = run(root)
    with pytest.raises(ReconNotPossible, match="the stub has no audited statement"):
        recon.tb_vs_statements(run_root, "acme", "FY26-YTD-SEP")
    run_root = run(root)
    with pytest.raises(ReconNotPossible, match="no annual period"):
        recon.tb_vs_statements(run_root, "acme")
