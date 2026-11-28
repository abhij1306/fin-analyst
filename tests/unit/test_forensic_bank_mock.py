"""SPEC-19 on the mock company: the main account's statements and the second account's
(`mock/extra/bank/`, `mock.generator.data_bank`) flag exactly the planted rows and no near miss;
a broken row names itself and the balance it expected; a round trip names the party's token; the
tuning log holds each bank test's thresholds and counts; two statements of one account loaded in
either order give the same flags; and a bank load makes only the bank runs stale."""

from pathlib import Path

import pytest

from fin.actor import USER
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence.bank import ingest_bank
from fin.diligence.sources import ingest_source
from fin.forensic import registry
from fin.forensic.status import tuning
from fin.paths import audit_db_path, company_db_path
from fin.services import forensic as services
from mock.generator import bank_extra, books, data, data_bank

CO = "corvane"
MOCK = Path(__file__).resolve().parents[2] / "mock"
MAIN = [
    MOCK / "dataroom" / "03_bank" / f"icici_{data.BANK_ACCOUNT}_{p}.xlsx"
    for p in ("fy24", "fy25", "fy26_h1")
]
SECOND = [MOCK / "extra" / "bank" / s.name for s in data_bank.STATEMENTS]
REGISTERS = MOCK / "dataroom" / "01_company" / "billing" / "corvane_registers_fy23_to_fy26_h1.xlsx"
BANK_TESTS = [name for name, t in registry.tests().items() if "bank" in t.kinds]


def run(root: Path) -> Run:
    return Run("test", {}, root)


def engagement(root: Path) -> Path:
    root.mkdir(parents=True)
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    (root / "logs").mkdir()
    ensure_database(company_db_path(root, CO), "company")
    with connect(company_db_path(root, CO), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, status, "
            "opened_run_id, opened_at) VALUES (?, ?, ?, ?, ?, 'open', 'r0', ?)",
            [
                ",".join(data.ANNUAL),
                data.QUERY_THRESHOLD,
                data.STUB,
                *data.PERIODS[data.STUB],
                utcnow(),
            ],
        )
    return root


def load(root: Path, files: list[Path]) -> None:
    config = load_config(root)
    for path in files:
        ingest_bank(run(root), config, CO, path, None)


def flags(root: Path, **filters: object) -> list[dict]:  # type: ignore[type-arg]
    rows = services.read_flags(run(root), root, CO, **filters)
    return [r for r in rows if r["test"] in BANK_TESTS]


def states(root: Path) -> dict[tuple[str, str], dict]:  # type: ignore[type-arg]
    return {(r["test"], r["period"]): r for r in services.read_status(run(root), root, CO)}


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = engagement(tmp_path_factory.mktemp("bank") / "data")
    ingest_source(run(root), load_config(root), CO, REGISTERS, "registers", None)
    load(root, MAIN + SECOND)
    services.run_tests(root, CO, via="cli", actor=USER)
    return root


def test_every_planted_row_is_flagged_and_no_near_miss(ran: Path) -> None:
    found = {
        (f["test"], f["period"], f["source"].split(" ")[0].rstrip(":"), f["row"], f["entry_date"])
        for f in flags(ran)
    }

    assert found == bank_extra.expected_flags(books.build())


def test_a_broken_row_names_itself_and_the_balance_it_expected(ran: Path) -> None:
    [chain] = flags(ran, test="bank_balance_chain")
    [planted] = [r for r in data_bank.ROWS if r.test == "bank_balance_chain" and r.flagged]

    assert chain["source"] == f"{data_bank.Q4.name} Statement!{bank_extra.row_of(planted)}"
    assert "expected ₹" in chain["reason"] and chain["amount"] == planted.off
    assert chain["account"].startswith("ACCT-") and chain["mapping_confirmed"] is None


def test_a_round_trip_names_the_party_by_its_token(ran: Path) -> None:
    [trip] = flags(ran, test="bank_round_trip")

    assert trip["party"].startswith("CUST-")
    assert f"both lines name {trip['party']}" in trip["reason"]


def test_each_bank_test_has_a_state_per_period(ran: Path) -> None:
    rows = states(ran)

    for test in BANK_TESTS:
        assert rows[(test, "FY23")]["text"] == "cannot run: needs a loaded bank statement for FY23"
    assert rows[("bank_period_end_credit", data.STUB)]["text"].startswith(
        "cannot run: needs the next period's bank statements"
    )
    assert rows[("bank_round", "FY25")]["text"] == "ran: 3 flagged, 3 at or above the threshold"


def test_the_tuning_log_holds_each_bank_tests_thresholds_and_counts(ran: Path) -> None:
    with connect(company_db_path(ran, CO)) as con:
        counts = tuning(con)

    assert counts["bank_round"]["FY25"]["thresholds"]["round_unit"] == "1000000"
    assert (
        counts["bank_cash"]["FY25"]["thresholds"]["cash_words"] == "CASH DEP, CASH WDL, ATM, SELF"
    )
    assert counts["bank_date_gap"]["FY26-YTD-SEP"]["thresholds"]["gap_days"] == "31"
    near = {
        test: counts[test]["FY25"]["near_misses"] for test in BANK_TESTS if test != "bank_date_gap"
    }
    assert near == {
        "bank_balance_chain": 2,  # the row printed 90,000 above, and the row below it
        "bank_date_order": 1,
        "bank_duplicate": 2,
        "bank_round": 1,
        "bank_round_trip": 1,
        "bank_own_transfer": 1,
        "bank_period_end_credit": 1,
        "bank_cash": 1,
    }
    assert counts["bank_date_gap"]["FY26-YTD-SEP"]["near_misses"] == 1


def _shown(root: Path) -> set[tuple]:  # type: ignore[type-arg]
    return {(f["test"], f["period"], f["source"], f["amount"], f["reason"]) for f in flags(root)}


def test_statements_loaded_in_either_order_give_the_same_flags(tmp_path: Path) -> None:
    roots = []
    for name, files in (("forward", SECOND), ("back", SECOND[::-1])):
        root = engagement(tmp_path / name)
        load(root, files)
        services.run_tests(root, CO, via="cli", actor=USER)
        roots.append(root)

    assert _shown(roots[0]) == _shown(roots[1])
    assert {f[0] for f in _shown(roots[0])} >= {"bank_period_end_credit", "bank_date_gap"}


def test_a_bank_load_makes_only_the_bank_runs_stale(tmp_path: Path) -> None:
    root = engagement(tmp_path / "data")
    load(root, SECOND[:1])
    services.run_tests(root, CO, via="cli", actor=USER)
    load(root, SECOND[1:])
    rows = states(root)

    assert rows[("bank_round", "FY25")]["state"] == "stale"
    assert rows[("je_round", "FY25")]["state"] == "cannot_run"  # no Ledger Vouchers: not stale
