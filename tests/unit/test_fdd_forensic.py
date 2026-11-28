"""SPEC-21 on the mock company (mock/, tests/unit/forensic_mock.py): FDD findings of area forensic
rest on forensic flags (fin.fdd.finding_flags), and the FDD report's Forensic tests section
(fin.report.forensic_section) lists every test with the counts mock/EXPECTED.md gives, the planted
flags in amount order, the queries on flags and the forensic findings; stale runs show as stale."""

import re
from pathlib import Path
from typing import Any

import pytest

from fin.actor import USER
from fin.audit.errors import FddFindingInvalid, NarrativeJudges
from fin.config import Display
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.fdd import findings
from fin.forensic import registry, settings
from fin.forensic.handoff import raise_from_flags
from fin.paths import company_db_path
from fin.report import forensic_section
from fin.report.fdd_render import SECTIONS
from fin.report.lint import TOKEN, judging_lines, lint_fdd
from fin.services import forensic as services
from mock.generator import books, data
from mock.generator.expected_report import flag_counts, planted_order
from tests.unit import forensic_mock as fm

CO = fm.COMPANY
RUPEES = Display("rupees", 0, "minus", "INR")
FINDING = {
    "area": "forensic",
    "title": "Journals in round amounts between two expense ledgers",
    "observation": "Two journals move round amounts from rent to hosting with no narration.",
    "impact_type": "none",
    "severity": "medium",
    "deal_consideration": "further_work",
    "evidence": "fin forensic flags, test je_round",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fm.build(tmp_path_factory.mktemp("fdd-forensic") / "data")


@pytest.fixture(scope="module")
def ran(built: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = fm.copy(built, tmp_path_factory.mktemp("fdd-forensic-ran") / "data")
    services.run_tests(root, CO, via="cli", actor=USER)
    return root


@pytest.fixture
def fresh(ran: Path, tmp_path: Path) -> Path:
    return fm.copy(ran, tmp_path / "data")


def report(root: Path, drafts: bool = False) -> tuple[dict[str, Any], str]:
    """The section's data and Markdown, as the FDD report reads them (its latest engagement)."""
    with connect(company_db_path(root, CO)) as con:
        [engagement] = fetch_dicts(con, "SELECT * FROM engagement")
        rows = findings.collect(con, engagement["engagement_id"], drafts)
        forensic = forensic_section.collect(con, root, CO, engagement, rows)
    shown = {"forensic": forensic, "stub": data.STUB, "drafts": drafts}
    return forensic, forensic_section.section(shown, RUPEES)


def je_round_ids(root: Path) -> list[int]:
    return [f["flag_id"] for f in services.read_flags(fm.run(root), root, CO, test="je_round")]


def propose(root: Path, **changes: Any) -> dict[str, Any]:
    return findings.propose(
        fm.run(root), CO, {**FINDING, "flag_ids": je_round_ids(root), **changes}
    )


def test_with_no_run_the_section_says_so(built: Path) -> None:
    forensic, text = report(built)

    assert not forensic["ran"]
    assert "No forensic test has run" in text
    assert SECTIONS["forensic"] == "Forensic tests"


def test_every_test_is_listed_with_the_expected_counts(ran: Path) -> None:
    forensic, text = report(ran)
    states = {(r["test"], r["period"]): r for r in forensic["tests"]}
    expected = {k: n for k, n in flag_counts(books.build()).items() if k[0].startswith("je_")}

    assert {t for t, _ in states} == set(registry.tests())
    assert len(states) == len(registry.tests()) * len(data.PERIODS)
    for name, test in registry.tests().items():
        assert f"{test.title} ({name})" in text
    got = {k: r["flagged"] for k, r in states.items() if k[0].startswith("je_") and r["flagged"]}
    assert got == expected


def test_the_planted_flags_are_listed_in_amount_order(ran: Path) -> None:
    forensic, text = report(ran)

    for test in {row[0] for row in planted_order()}:
        planted = [r for r in planted_order() if r[0] == test]
        vouchers = {(v, ledger) for _, v, ledger, _ in planted}
        shown = [
            (f["voucher_no"], f["ledger"] or "-", abs(f["amount"]) if f["amount"] else None)
            for f in forensic["flags"][test]["shown"]
        ]
        amounts = [a for v, ledger, a in shown if (v, ledger) in vouchers]
        assert amounts == [a for *_, a in planted], test
        known = [a for *_, a in shown if a is not None]
        assert known == sorted(known, reverse=True)
    assert "corvane_ledger_vouchers_fy25.xlsx" in text and "Donations" in text  # ledger as filed


def test_parties_are_tokens_and_the_text_does_not_judge(ran: Path) -> None:
    forensic, text = report(ran)
    parties = {f["party"] for flags in forensic["flags"].values() for f in flags["shown"]}

    assert all(re.fullmatch(TOKEN, p) for p in parties if p)
    assert judging_lines(text) == []
    assert not re.search(r"\b(?:risk|score|rating)", text, re.IGNORECASE)


def test_a_stale_run_is_shown_as_stale(fresh: Path) -> None:
    settings.set_value(fm.run(fresh), fresh, CO, "round_unit", "100000", USER)
    _, text = report(fresh)

    assert "stale (data or settings changed since; last ran: 2 flagged" in text
    assert "(stale run)" in text


def test_a_forensic_finding_rests_on_current_flags(fresh: Path) -> None:
    ids = je_round_ids(fresh)
    for changes, problem in (
        ({"flag_ids": []}, "rests on flags"),
        ({"area": "other"}, "only a finding of area forensic"),
        ({"flag_ids": [99999]}, "not flags of the open engagement"),
        ({"observation": "The flags make it a poor investment."}, "matched: 'poor investment'"),
    ):
        with pytest.raises(FddFindingInvalid, match=problem):
            propose(fresh, **changes)

    row = propose(fresh)
    assert row["flag_ids"] == sorted(ids) and row["amount"] is None
    assert "forensic flags: #" in findings.format_finding(row)
    services.run_tests(fresh, CO, "je_round", "FY25", via="cli", actor=USER)
    with pytest.raises(FddFindingInvalid, match="not in the latest run"):
        propose(fresh, flag_ids=ids)
    kept = findings.update(fm.run(fresh), CO, row["finding_id"], {"title": "Round journals"})
    assert kept["flag_ids"] == sorted(ids)  # a stored link stays when the test runs again


def test_the_amount_comes_from_the_query_on_the_flags_not_the_flags(fresh: Path) -> None:
    ids = je_round_ids(fresh)
    query = raise_from_flags(fm.run(fresh), fresh, CO, ids, "Please explain these journals.")
    row = propose(fresh, query_id=query["query_id"])

    assert row["amount"] == query["amount"] and row["amount_source"].startswith("query #")
    forensic, text = report(fresh, drafts=True)
    assert [q["query_id"] for q in forensic["queries"]] == [query["query_id"]]
    assert f"#{query['query_id']} (draft)" in text


def test_a_draft_shows_only_with_drafts_and_a_final_one_always(fresh: Path) -> None:
    row = propose(fresh)

    assert FINDING["title"] not in report(fresh)[1]
    assert FINDING["title"] in report(fresh, drafts=True)[1]
    findings.set_status(fm.run(fresh), CO, [row["finding_id"]], "final")
    assert FINDING["title"] in report(fresh)[1]


def test_the_narrative_of_the_section_is_linted() -> None:
    with pytest.raises(NarrativeJudges):
        lint_fdd("## Forensic tests\n\nThe flags make it a bad investment.\n", "n")
    lint_fdd("## Forensic tests\n\nThe tests flagged journals near the year end.\n", "n")
