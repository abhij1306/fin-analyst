"""FDD findings (fin.fdd.findings) and the FDD report (fin.report.fdd_render, docs/fdd.md F8) on
the synthetic engagement of tests/qoe_fixtures.py: amounts only from linked evidence, only the
user's final findings in the report, the narrative lint (typed numbers, valuation and buy/don't-buy
language) and the sections that say what is not loaded. Quarto is not run here."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import (
    FddFindingInvalid,
    FddFindingUnknown,
    NarrativeHasTypedNumbers,
    NarrativeJudges,
)
from fin.db.conn import connect
from fin.diligence.mask import FREE_TEXT_COLUMNS, PREFIX
from fin.fdd import findings
from fin.paths import company_db_path
from fin.report import dd_render
from fin.report.fdd_data import collect
from fin.report.fdd_render import FDD, SECTIONS, make_fdd_report
from fin.report.fdd_tables import key_findings
from fin.report.lint import TOKEN, judging_lines, lint_fdd
from tests.qoe_fixtures import build_root, run

FINDING = {
    "area": "qoe",
    "title": "One-off legal fees added back",
    "observation": "Legal fees for a settled dispute are booked in other expenses.",
    "implication": "Adjusted EBITDA depends on the fees not recurring.",
    "impact_type": "ebitda",
    "severity": "high",
    "deal_consideration": "spa_protection",
    "evidence": "Notes to accounts p. 12; adjustment #1",
    "adj_id": 1,
}
NARRATIVE = """
## Key findings

One high severity finding on the legal fees; it calls for SPA protection.

## Net debt and debt-like items

Net debt at the stub end was {{figure:net_debt:FY26-YTD-SEP}}.

## Quality of earnings

Adjusted EBITDA {{figure:adjusted_ebitda:FY25}}.
"""


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_root(tmp_path_factory.mktemp("fdd-report"))


@pytest.fixture
def root(base: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(base, tmp_path / "data"))


def propose(root: Path, **changes: Any) -> dict[str, Any]:
    return findings.propose(run(root), "acme", {**FINDING, **changes})


def in_report(root: Path, drafts: bool = False) -> list[int]:
    """The ids the report reads (`findings.collect`; the whole report data takes long to read)."""
    with connect(company_db_path(root, "acme")) as con:
        return [r["finding_id"] for r in findings.collect(con, 1, drafts)]


def test_the_amount_comes_only_from_the_linked_evidence(root: Path) -> None:
    adjusted = propose(root)
    queried = propose(root, adj_id=None, query_id=1, severity="medium", area="working_capital")
    bare = propose(root, adj_id=None, severity="low", deal_consideration="none")

    assert (adjusted["amount"], adjusted["amount_source"]) == (100000, "adjustment #1 (accepted)")
    assert (queried["amount"], queried["amount_source"]) == (150000, "query #1")
    assert queried["management_response"] is None and queried["query_status"] == "approved"
    assert (bare["amount"], bare["amount_source"]) == (None, None)
    assert adjusted["status"] == "draft"
    listed = findings.list_findings(run(root), "acme")
    assert [r["severity"] for r in listed] == ["high", "medium", "low"]
    assert "no answer yet (approved)" in findings.format_finding(queried)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"observation": "Fees of 12 lakh were added back."}, "typed number"),
        ({"implication": "The enterprise value should be lower."}, "valuation"),
        ({"title": "Do not buy"}, "buy"),
        ({"severity": "critical"}, "severity 'critical'"),
        ({"evidence": " "}, "give the evidence"),
        ({"netdebt_item_id": 1}, "at most one"),
        ({"adj_id": 99}, "adjustments has no #99"),
        ({"adj_id": None, "recon_id": 99}, "recon_runs has no #99"),
    ],
)
def test_a_finding_at_fault_is_e970_and_nothing_is_stored(
    root: Path, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(FddFindingInvalid, match=message):
        propose(root, **changes)
    assert findings.list_findings(run(root), "acme", every=True) == []


def test_only_the_user_s_final_findings_reach_the_report(root: Path) -> None:
    first = propose(root)["finding_id"]
    second = propose(root, severity="low", title="Minor cut-off item", adj_id=2)["finding_id"]
    third = propose(root, adj_id=None, title="Dropped idea")["finding_id"]

    assert in_report(root) == []
    findings.set_status(run(root), "acme", [first], "final")
    findings.set_status(run(root), "acme", [third], "dropped")
    updated = findings.update(run(root), "acme", second, {"recon_id": 1})

    assert updated["adj_id"] is None and updated["amount_source"].startswith("reconciliation")
    assert in_report(root) == [first]
    assert in_report(root, drafts=True) == [first, second]
    with pytest.raises(FddFindingUnknown, match="must be draft"):
        findings.update(run(root), "acme", first, {"title": "Changed"})
    with pytest.raises(FddFindingUnknown, match="must be draft"):
        findings.set_status(run(root), "acme", [third], "final")
    with pytest.raises(FddFindingUnknown, match="not in the open engagement"):
        findings.set_status(run(root), "acme", [99], "dropped")
    findings.set_status(run(root), "acme", [first], "draft")
    assert in_report(root) == []


@pytest.fixture(scope="module")
def report_data(base: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One final and one draft finding, read once with --drafts (the report data takes long)."""
    root = Path(shutil.copytree(base, tmp_path_factory.mktemp("fdd-data") / "data"))
    findings.set_status(run(root), "acme", [propose(root)["finding_id"]], "final")
    propose(root, adj_id=2, severity="low", title="Minor cut-off item")
    return collect(root, "acme", "standalone", drafts=True)


def test_the_report_has_the_findings_by_severity_and_every_section(
    report_data: dict[str, Any],
) -> None:
    lint_fdd(NARRATIVE, "n")
    qmd = dd_render.build("acme", "standalone", "rupees", NARRATIVE, report_data, FDD).qmd

    assert "{{" not in qmd
    assert "acme: FDD report" in qmd
    assert qmd.index("### High severity") < qmd.index("### Low severity")
    assert "#### One-off legal fees added back (finding #1) [final]" in qmd
    assert "#### Minor cut-off item (finding #2) [draft]" in qmd
    assert "**Draft copy**" in qmd
    assert "amount: ₹100,000.00 (adjustment #1 (accepted))" in qmd
    assert "amount: ₹40,000.00 (adjustment #2 (proposed))" in qmd
    assert "Deal consideration: SPA indemnity or warranty" in qmd
    assert "Management response: no query raised" in qmd
    assert "Adjusted EBITDA ₹1,600,000.00." in qmd
    for title in SECTIONS.values():
        assert f"## {title}" in qmd
    # sections whose data is not loaded say so
    assert "Not loaded: no sample drawn" in qmd
    assert "Not loaded: no notes or notices read" in qmd
    assert "Scope and limitations" in qmd


def test_the_fdd_stub_names_its_sections_and_figures(report_data: dict[str, Any]) -> None:
    text = dd_render.stub("acme", report_data, FDD)
    assert "## Net debt and debt-like items" in text
    assert "net_debt_debt_like" in text and "--fdd" in text
    assert "no buy or" in text


@pytest.mark.parametrize(
    "line",
    [
        "The equity value is lower than the price.",
        "We rate it an attractive investment.",
        "An EV/EBITDA multiple applies.",
        "The buyer should not acquire the company.",
        "On balance we would buy.",
        "The business looks overvalued.",
        "We recommend buying the company.",
    ],
)
def test_valuation_rating_and_buy_language_is_e972(line: str) -> None:
    with pytest.raises(NarrativeJudges, match="line 3"):
        lint_fdd(f"## Key findings\n\n{line}\n", "n")


def test_stored_text_is_escaped_in_the_report() -> None:
    row = {**FINDING, "finding_id": 7, "status": "final", "amount": None, "query_id": None,
           "observation": "<script>alert(1)</script> found"}  # fmt: skip
    text = key_findings({"fdd_findings": [row], "drafts": False}, "rupees")
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_tokens_are_names_not_typed_numbers(root: Path) -> None:
    row = propose(root, observation="CUST-0001 and LEDG-0012 have no contract on file.")
    assert row["observation"].startswith("CUST-0001")
    lint_fdd("## Revenue, contracts and visibility\n\nVEND-0002 has no contract.\n", "n")
    assert ("fdd_findings", "observation") in FREE_TEXT_COLUMNS
    assert set(PREFIX.values()) <= set(TOKEN[TOKEN.index("(?:") + 3 : TOKEN.index(")")].split("|"))


def test_fdd_words_that_are_not_judging_pass() -> None:
    text = (
        "The buyer may seek an SPA indemnity. A share buy-back was approved. Investments are at "
        "fair value. No actuarial valuation was done. Severity is high; this is a price point."
    )
    assert judging_lines(text) == []
    with pytest.raises(NarrativeHasTypedNumbers):
        lint_fdd("## Tax\n\nTax rose 12%.\n", "n")


@pytest.mark.quarto
@pytest.mark.skipif(shutil.which("quarto") is None, reason="needs Quarto (CI `outputs` job)")
def test_quarto_renders_the_fdd_report_in_all_three_formats(root: Path) -> None:
    findings.set_status(run(root), "acme", [propose(root)["finding_id"]], "final")
    first = make_fdd_report(run(root), "acme", "standalone", False, False)
    narrative = Path(first["narrative_path"])
    assert narrative.name == "fdd_narrative.md"
    narrative.write_text(NARRATIVE, encoding="utf-8")

    result = make_fdd_report(run(root), "acme", "standalone", False, False)

    assert result["status"] == "rendered", result
    for name in ("fdd.html", "fdd.docx", "fdd.pdf", "fdd_data.json"):
        assert (narrative.parent / name).stat().st_size > 0
    html = (narrative.parent / "fdd.html").read_text(encoding="utf-8")
    assert "One-off legal fees added back" in html and "Scope and limitations" in html
