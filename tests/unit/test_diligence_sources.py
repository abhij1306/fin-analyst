"""Diligence source files (fin.diligence.sources): a synthetic Tally trial balance and Ledger
Vouchers load with sheet!row lineage and masked party names; what cannot load is refused with its
code and nothing is loaded."""

import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import (
    DuplicateFile,
    GroupsOnlyTrialBalance,
    LayoutUnknown,
    MaskedTableRead,
    ParseError,
    SourcePeriodInvalid,
    TrialBalanceNeeded,
)
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence.sources import ingest_source
from fin.findings import accept
from fin.paths import company_db_path
from fin.query.run import check_select
from tests.tally_fixtures import (
    BANK,
    CUSTOMERS,
    VENDORS,
    VOUCHERS,
    write_ledger_vouchers,
    write_trial_balance,
    write_unknown_layout,
)

REAL_NAMES = (*CUSTOMERS, *VENDORS)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with company acme and an open engagement for FY25, made once (creating a
    company database takes seconds): each test copies it."""
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
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def load(root: Path, path: Path, kind: str, profile: str | None = None) -> list[str]:
    run = Run("ingest", {}, root)
    return ingest_source(run, load_config(root), "acme", path, kind, profile).lines


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def test_ledger_block_without_vouchers_counts_as_in_the_ledger_vouchers(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fin.diligence.tb_checks import _missing_from_vouchers

    monkeypatch.setitem(VOUCHERS, "Share Capital", [])  # only an opening balance in the year
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    ledgers = [*CUSTOMERS, *VENDORS, "Software Sales", "Share Capital", "GST Payable"]
    ledgers += [BANK, "Rent", "Salaries"]

    lines = load(root, write_ledger_vouchers(tmp_path / "lv.xlsx", ledgers), "ledger_vouchers")

    assert lines[-1] == "every FY25 trial balance ledger is in it"
    count = "SELECT count(*) FROM {} WHERE ledger = 'Share Capital'"
    assert rows(root, count.format("ledger_entries")) == [(0,)]
    assert rows(root, count.format("ledger_blocks")) == [(1,)]
    with connect(company_db_path(root, "acme")) as con:
        assert _missing_from_vouchers(con, 1, 2) == []


def test_trial_balance_loads_with_lineage_and_masked_parties(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")

    assert lines[0] == (
        "loaded as source file_id 1 (trial_balance FY25, profile tally_trial_balance)"
    )
    assert (
        lines[1] == "trial balance FY25: 10 ledgers in 9 groups, 4 names newly masked, 0 findings"
    )
    assert rows(
        root,
        "SELECT kind, period, period_start::VARCHAR, period_end::VARCHAR, basis, status, "
        "archive_path LIKE 'archive/acme/%.xlsx' FROM source_files",
    ) == [("trial_balance", "FY25", "2024-04-01", "2025-03-31", "audited", "loaded", True)]
    ledgers = rows(
        root,
        'SELECT "row", group_name, parent_group, ledger, closing_debit - closing_credit '
        "FROM trial_balance WHERE row_type = 'ledger' AND group_name IN "
        "('Sundry Debtors', 'Sundry Creditors', 'Capital Account') ORDER BY \"row\"",
    )
    assert ledgers == [
        (7, "Capital Account", None, "Share Capital", Decimal("-500000.00")),
        (10, "Sundry Creditors", "Current Liabilities", "VEND-0001", Decimal("-120000.00")),
        (11, "Sundry Creditors", "Current Liabilities", "VEND-0002", Decimal("-60000.00")),
        (16, "Sundry Debtors", "Current Assets", "CUST-0001", Decimal("200000.00")),
        (17, "Sundry Debtors", "Current Assets", "CUST-0002", Decimal("100000.00")),
    ]
    assert rows(root, "SELECT DISTINCT sheet FROM trial_balance") == [("Trial Balance",)]
    assert rows(
        root, 'SELECT row_type, group_name, parent_group FROM trial_balance WHERE "row" = 15'
    ) == [("group", "Sundry Debtors", "Current Assets")]
    masked = rows(root, "SELECT token, kind, real_value FROM mask_values ORDER BY token")
    assert masked == [
        ("CUST-0001", "customer", CUSTOMERS[0]),
        ("CUST-0002", "customer", CUSTOMERS[1]),
        ("VEND-0001", "vendor", VENDORS[0]),
        ("VEND-0002", "vendor", VENDORS[1]),
    ]
    stored = " ".join(str(r) for r in rows(root, "SELECT * FROM trial_balance"))
    assert not any(name in stored for name in REAL_NAMES)
    assert not any(name in " ".join(lines) for name in REAL_NAMES)


def test_ledger_vouchers_take_the_ledger_from_the_block_and_mask_names(
    root: Path, tmp_path: Path
) -> None:
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    ledgers = [CUSTOMERS[0], "Software Sales", VENDORS[0], "Neem Consulting", "Rent"]

    lines = load(root, write_ledger_vouchers(tmp_path / "lv.xlsx", ledgers), "ledger_vouchers")

    assert lines[1] == "ledger vouchers FY25: 7 lines in 5 ledgers, 1 names newly masked"
    assert lines[2] == (
        "1 ledgers not in the FY25 trial balance, masked because fin cannot tell whether they "
        "are parties: LEDG-0001"
    )
    assert lines[3] == (
        "6 trial balance ledgers are missing from it (ask for the Ledger Vouchers export with all "
        "ledgers): Share Capital, VEND-0002, GST Payable, CUST-0002, Axis Bank Current Account, "
        "Salaries"
    )
    entries = rows(
        root,
        'SELECT "row", ledger, entry_date::VARCHAR, particulars, voucher_type, voucher_no, '
        'debit - credit FROM ledger_entries ORDER BY "row"',
    )
    assert entries[:3] == [
        (7, "CUST-0001", "2024-05-12", "Software Sales", "Sales", "101", Decimal("500000.00")),
        (8, "CUST-0001", "2024-05-30", BANK, "Receipt", "7", Decimal("-300000.00")),
        (12, "Software Sales", "2024-05-12", "CUST-0001", "Sales", "101", Decimal("-500000.00")),
    ]
    assert rows(root, "SELECT DISTINCT sheet FROM ledger_entries") == [("Ledger Vouchers",)]
    assert len(entries) == sum(len(VOUCHERS[name]) for name in ledgers)
    assert ("LEDG-0001",) in rows(root, "SELECT DISTINCT ledger FROM ledger_entries")
    stored = " ".join(str(r) for r in entries)
    assert not any(name in stored for name in (*REAL_NAMES, "Neem Consulting"))


def test_a_group_not_equal_to_its_ledgers_is_a_finding(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_trial_balance(tmp_path / "tb.xlsx", plant=True), "trial_balance")

    assert lines[1].endswith("1 findings")
    assert rows(
        root,
        "SELECT doc_id, file_id, check_name, label, fin_value, other_value, difference, "
        "fin_source, status FROM findings",
    ) == [
        (None, 1, "tb_group_sum", "Sundry Debtors", Decimal("310000.00"), Decimal("300000.00"),
         Decimal("-10000.00"), "Trial Balance!15", "open"),
    ]  # fmt: skip
    assert rows(root, 'SELECT closing_debit FROM trial_balance WHERE "row" = 15') == [
        (Decimal("310000.00"),)
    ]


def test_a_groups_only_trial_balance_is_refused(root: Path, tmp_path: Path) -> None:
    path = write_trial_balance(tmp_path / "tb.xlsx", groups_only=True)

    with pytest.raises(GroupsOnlyTrialBalance, match="Detailed mode"):
        load(root, path, "trial_balance")

    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]
    assert rows(root, "SELECT count(*) FROM trial_balance") == [(0,)]


def test_an_unknown_layout_lists_the_header_lines_and_no_data(root: Path, tmp_path: Path) -> None:
    with pytest.raises(LayoutUnknown) as error:
        load(root, write_unknown_layout(tmp_path / "x.xlsx"), "trial_balance")

    message = str(error.value)
    assert message.startswith("E807: x.xlsx: no trial_balance column profile matches its headers")
    assert "row 1: Account balances / row 2: Account | Balance" in message
    assert CUSTOMERS[0] not in message


def test_e807_never_lists_a_ledger_block_heading_and_masks_known_names(
    root: Path, tmp_path: Path
) -> None:
    lv = write_ledger_vouchers(tmp_path / "lv.xlsx", [CUSTOMERS[0]])
    with pytest.raises(LayoutUnknown) as error:
        load(root, lv, "trial_balance")
    assert "row 4: Date | Particulars | Vch Type | Vch No. | Debit | Credit" in str(error.value)
    assert CUSTOMERS[0] not in str(error.value)

    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    titled = write_unknown_layout(tmp_path / "x.xlsx", title=f"Ledger: {CUSTOMERS[0]}")
    with pytest.raises(LayoutUnknown) as error:
        load(root, titled, "trial_balance")
    assert "row 1: Ledger: CUST-0001 / row 2: Account | Balance" in str(error.value)


def test_a_voucher_row_whose_date_cannot_be_read_is_refused(root: Path, tmp_path: Path) -> None:
    load(root, write_trial_balance(tmp_path / "tb.xlsx"), "trial_balance")
    path = write_ledger_vouchers(tmp_path / "lv.xlsx", ["Rent"], date_text="05/05/2024")

    with pytest.raises(ParseError, match=r"Ledger Vouchers!7: a row with amounts but no date"):
        load(root, path, "ledger_vouchers")

    assert rows(root, "SELECT count(*) FROM ledger_entries") == [(0,)]


def test_an_unknown_profile_is_e807(root: Path, tmp_path: Path) -> None:
    path = write_trial_balance(tmp_path / "tb.xlsx")
    with pytest.raises(LayoutUnknown, match="no column profile 'nope'"):
        load(root, path, "trial_balance", "nope")
    with pytest.raises(LayoutUnknown, match="reads ledger_vouchers, not trial_balance"):
        load(root, path, "trial_balance", "tally_ledger_vouchers")


def test_a_period_outside_the_engagement_is_refused(root: Path, tmp_path: Path) -> None:
    path = write_trial_balance(tmp_path / "tb.xlsx", period="1-Apr-2023 to 31-Mar-2024")
    with pytest.raises(
        SourcePeriodInvalid, match=r"\(the fiscal year FY24\), which is not a period"
    ):
        load(root, path, "trial_balance")
    stub = write_trial_balance(tmp_path / "stub.xlsx", period="1-Apr-2024 to 30-Sep-2024")
    with pytest.raises(SourcePeriodInvalid, match="not a full fiscal year"):
        load(root, stub, "trial_balance")
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]


def test_ledger_vouchers_need_the_trial_balance_first(root: Path, tmp_path: Path) -> None:
    with pytest.raises(TrialBalanceNeeded):
        load(root, write_ledger_vouchers(tmp_path / "lv.xlsx", ["Rent"]), "ledger_vouchers")


def test_the_same_file_twice_is_refused_and_a_new_one_supersedes(
    root: Path, tmp_path: Path
) -> None:
    first = write_trial_balance(tmp_path / "tb.xlsx", plant=True)
    load(root, first, "trial_balance")
    with pytest.raises(DuplicateFile):
        load(root, first, "trial_balance")

    lines = load(root, write_trial_balance(tmp_path / "tb2.xlsx"), "trial_balance")

    assert lines[0].endswith("; supersedes file_id 1")
    assert rows(root, "SELECT file_id, status, superseded_by FROM source_files ORDER BY 1") == [
        (1, "superseded", 2),
        (2, "loaded", None),
    ]
    assert rows(root, "SELECT status FROM findings") == [("superseded",)]
    assert rows(root, "SELECT count(*) FROM mask_values") == [(4,)]  # same names, same tokens
    with pytest.raises(DuplicateFile, match="already loaded as source file_id 1"):
        load(root, first, "trial_balance")  # superseded, still the same file


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT real_value FROM main.Mask_Values",
        "SELECT * FROM query_table('mask_' || 'values')",
        "SELECT * FROM \"query\" ('SELECT * FROM mask_' || 'values')",
        "SELECT * FROM read_duckdb('acme.duckdb', table_name := 'mask_' || 'values')",
    ],
)
def test_fin_sql_refuses_to_read_mask_values(root: Path, sql: str) -> None:
    with connect(company_db_path(root, "acme")) as con, pytest.raises(MaskedTableRead):
        check_select(con, sql)


def test_fin_sql_still_reads_a_column_named_query(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con:
        check_select(con, "SELECT 1 AS query, (2) AS two")


def test_accepting_a_source_file_finding_reports_the_file(root: Path, tmp_path: Path) -> None:
    load(root, write_trial_balance(tmp_path / "tb.xlsx", plant=True), "trial_balance")

    out = accept(Run("findings accept", {}, root), "acme", [1])

    assert out == [{"file_id": 1, "file": "tb.xlsx", "open": 0, "status": "loaded"}]
    assert rows(root, "SELECT status FROM findings") == [("accepted",)]
