"""SPEC-18 PR 1 on the mock company (mock/, docs/decisions/forensic.md): every journal planted for
a forensic test is flagged and no near miss is; the rows of the books a test rightly flags are the
ones `mock.generator.data.FORENSIC_EXISTING` lists; the status of each test and period; a run going
stale when its data or a setting changes, with its flags kept; and the hand-off to a query."""

from pathlib import Path

import pytest

from fin.actor import USER
from fin.audit.errors import ForensicFlagNotQueryable, ForensicFlagUnknown
from fin.db.conn import connect
from fin.forensic import settings
from fin.forensic.handoff import raise_from_flags
from fin.forensic.status import tuning
from fin.paths import company_db_path
from fin.services import forensic as services
from fin.services.result import Done
from mock.generator import data
from tests.unit import forensic_mock as fm

CO = fm.COMPANY


def period_of(day: object) -> str:
    [found] = [p for p, (start, end) in data.PERIODS.items() if start <= day <= end]  # type: ignore[operator]
    return found


def expected() -> set[tuple[str, str, str, str | None]]:
    """(test, period, voucher number, ledger) of every flag the mock should give."""
    out: set[tuple[str, str, str, str | None]] = set()
    for planted in data.FORENSIC:
        for flagged in planted.flags:
            gap = planted.test == "je_gap"
            ledger = None if gap else flagged
            out.add(
                (planted.test, period_of(planted.when), flagged if gap else planted.number, ledger)
            )
    out |= {
        (test, period, voucher, ledger)
        for test, period, voucher, ledger, _ in data.FORENSIC_EXISTING
    }
    return out


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Done]:
    root = fm.build(tmp_path_factory.mktemp("forensic") / "data")
    done = services.run_tests(root, CO, via="cli", actor=USER)
    assert isinstance(done, Done), done
    return root, done


@pytest.fixture
def fresh(ran: tuple[Path, Done], tmp_path: Path) -> Path:
    return fm.copy(ran[0], tmp_path / "data")


def flags(root: Path, **filters: object) -> list[dict]:  # type: ignore[type-arg]
    return services.read_flags(fm.run(root), root, CO, **filters)


def states(root: Path) -> dict[tuple[str, str], dict]:  # type: ignore[type-arg]
    return {(r["test"], r["period"]): r for r in services.read_status(fm.run(root), root, CO)}


def test_every_planted_journal_is_flagged_and_no_near_miss(ran: tuple[Path, Done]) -> None:
    found = {(f["test"], f["period"], f["voucher_no"], f["ledger"]) for f in flags(ran[0])}

    assert found == expected()
    near = {p.number for p in data.FORENSIC if p.why.startswith("near miss")}
    assert not near & {voucher for _, _, voucher, _ in found}


def test_flags_carry_their_source_account_and_are_largest_first(ran: tuple[Path, Done]) -> None:
    rows = flags(ran[0])
    amounts = [abs(f["amount"]) for f in rows if f["amount"] is not None]

    assert amounts == sorted(amounts, reverse=True)
    assert rows[-1]["test"] == "je_gap" and rows[-1]["amount"] is None  # a gap has no amount
    [donation] = [f for f in rows if f["ledger"] == "Donations"]
    assert donation["source"].startswith("corvane_ledger_vouchers_fy25.xlsx ")
    assert (donation["account"], donation["mapping_confirmed"]) == ("other_expenses", True)
    assert all(f["over_threshold"] and not f["stale"] and f["query_id"] is None for f in rows)


def test_each_test_and_period_has_a_state(ran: tuple[Path, Done]) -> None:
    rows = states(ran[0])

    assert len(rows) == 11 * len(data.PERIODS)
    assert rows[("je_round", "FY25")]["text"] == ("ran: 2 flagged, 2 at or above the threshold")
    assert rows[("je_benford", "FY25")]["text"].startswith("too few entries: ")
    assert rows[("je_gap", "FY24")]["text"] == "too few entries: 19 of 20 needed"
    assert rows[("je_period_end", "FY23")]["text"].startswith("cannot run: needs the previous")
    assert rows[("je_reversed", data.STUB)]["text"].startswith("cannot run: needs the next")


def test_the_log_lines_name_no_ledger_or_party(ran: tuple[Path, Done]) -> None:
    lines = "\n".join(ran[1].lines)
    names = [*data.LEDGERS, *(c.ledger for c in data.CUSTOMERS.values())]

    assert "je_period_end FY25: ran, 4 flagged (4 at or above the threshold)" in lines
    assert not [name for name in names if name in lines]


def test_tuning_counts_for_the_eval_bundle(ran: tuple[Path, Done]) -> None:
    with connect(company_db_path(ran[0], CO)) as con:
        counts = tuning(con)

    assert counts["je_round"]["FY25"]["flagged"] == 2
    assert counts["je_round"]["FY25"]["thresholds"]["round_unit"] == "1000000"
    assert counts["je_unusual_pair"]["FY25"]["near_misses"] == 4


def test_a_setting_change_makes_its_tests_stale_and_keeps_their_flags(fresh: Path) -> None:
    settings.set_value(fm.run(fresh), fresh, CO, "round_unit", "100000", USER)
    rows = states(fresh)

    assert rows[("je_round", "FY25")]["state"] == "stale"
    assert rows[("je_round", "FY25")]["text"].startswith("stale (data or settings changed since")
    assert rows[("je_weekend", "FY25")]["state"] == "ran"
    kept = flags(fresh, test="je_round")
    assert len(kept) == 2 and all(f["stale"] for f in kept)
    services.run_tests(fresh, CO, "je_round", "FY25", via="cli", actor=USER)
    assert states(fresh)[("je_round", "FY25")]["state"] == "ran"


def test_a_mapping_change_makes_every_run_stale(fresh: Path) -> None:
    with connect(company_db_path(fresh, CO), write=True) as con:
        con.execute("UPDATE ledger_accounts SET account = 'other_income' WHERE ledger = 'Rent'")

    assert {r["state"] for r in states(fresh).values() if r["outcome"]} == {"stale"}


def test_another_ledger_vouchers_file_makes_the_runs_stale(tmp_path: Path) -> None:
    root = fm.build(tmp_path / "data", tuple(data.ANNUAL))
    services.run_tests(root, CO, via="cli", actor=USER)
    before = states(root)
    assert before[("je_reversed", "FY25")]["state"] == "cannot_run"  # no next period yet

    config = fm.load_config(root)
    for kind, folder, files in (
        ("trial_balance", "trial_balances", fm.TRIAL),
        ("ledger_vouchers", "ledgers", fm.VOUCHERS),
    ):
        path = fm.ROOM / folder / files[data.STUB]
        fm.ingest_source(fm.run(root), config, CO, path, kind, None)

    after = states(root)
    assert after[("je_reversed", "FY25")]["state"] == "stale"
    assert after[("je_round", "FY25")]["state"] == "stale"
    assert len(flags(root, test="je_round")) == 2  # the earlier flags are kept


def test_flags_go_to_one_query_and_show_it(fresh: Path) -> None:
    ids = [f["flag_id"] for f in flags(fresh, test="je_round")]
    raise_from_flags(fm.run(fresh), fresh, CO, ids, "Please explain these journals.")

    shown = flags(fresh, test="je_round")
    assert {f["query_status"] for f in shown} == {"draft"}
    assert len({f["query_id"] for f in shown}) == 1
    with pytest.raises(ForensicFlagNotQueryable):
        raise_from_flags(fm.run(fresh), fresh, CO, ids, "Again?")
    services.run_tests(fresh, CO, "je_round", "FY25", via="cli", actor=USER)
    again = flags(fresh, test="je_round")
    assert {f["flag_id"] for f in again}.isdisjoint(ids)  # a new run, new flags
    assert {f["query_id"] for f in again} == {shown[0]["query_id"]}  # the query stays on them
    with pytest.raises(ForensicFlagNotQueryable):
        raise_from_flags(fm.run(fresh), fresh, CO, [f["flag_id"] for f in again], "Again?")


def test_flags_of_several_periods_or_a_stale_run_are_refused(fresh: Path) -> None:
    rare = flags(fresh, test="je_rare_ledger")
    periods = {f["period"]: f["flag_id"] for f in rare}
    with pytest.raises(ForensicFlagNotQueryable):
        raise_from_flags(fm.run(fresh), fresh, CO, [periods["FY23"], periods["FY25"]], "Why?")
    with pytest.raises(ForensicFlagUnknown):
        raise_from_flags(fm.run(fresh), fresh, CO, [99999], "Why?")
    settings.set_value(fm.run(fresh), fresh, CO, "rare_max_lines", "1", USER)
    with pytest.raises(ForensicFlagUnknown):
        raise_from_flags(fm.run(fresh), fresh, CO, [periods["FY25"]], "Why?")
