"""The diligence report (fin.report.dd_render) on the synthetic engagement of
tests/qoe_fixtures.py: references filled from the database, every figure traced to rows, the stub
and narrative rules. Quarto is not run here (CI renders the real files)."""

import shutil
from pathlib import Path

import pytest

from fin.audit.errors import FigureReferenceUnknown, NarrativeHasTypedNumbers
from fin.config import load_config
from fin.reconcile.recast_run import run_recast
from fin.report import dd_render
from fin.report.dd_data import FIGURES, collect
from fin.report.lint import FIGURE, lint
from tests.qoe_fixtures import build_root, run

NARRATIVE = """
## Basis and gates

The stub is unaudited management accounts.

## Adjusted EBITDA

- Reported {{figure:reported_ebitda:FY25}}, adjusted {{figure:adjusted_ebitda:FY25}} in FY25.
- Proposed, not applied: {{figure:proposed_adjustments:FY25}}.

## Revenue: core and project revenue

Project revenue was {{figure:passthrough_revenue:FY25}}; core {{figure:core_revenue:FY25}}.

## Working capital

Net working capital {{figure:net_working_capital:FY25}};
stub {{figure:net_working_capital:FY26-YTD-SEP}}.
"""


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_root(tmp_path_factory.mktemp("qoe-report"))


def built(root: Path, narrative: str = NARRATIVE) -> dd_render.Built:
    data = collect(root, "acme", "standalone")
    lint(narrative, "n", FIGURE)
    return dd_render.build("acme", "standalone", "rupees", narrative, data)


def test_the_report_is_filled_from_the_database_with_every_section(root: Path) -> None:
    out = built(root)
    qmd = out.qmd
    assert "Reported ₹1,500,000.00, adjusted ₹1,600,000.00 in FY25." in qmd
    assert "Proposed, not applied: ₹40,000.00." in qmd
    assert "Project revenue was ₹860,000.00; core ₹250,000.00." in qmd
    assert "Net working capital ₹564,000.00;\nstub ₹650,000.00." in qmd
    # no placeholder is left, the stub is labelled and the gates are listed
    assert "{{" not in qmd
    assert "FY26-YTD-SEP (unaudited management accounts)" in qmd
    assert "G1 Ingestion | open" in qmd
    assert "Pass-through (project revenue)" in qmd
    assert "## Customers, sectors and pricing" in qmd
    assert "One-off legal fees" in qmd and "proposed (not applied)" in qmd
    assert "registers.xlsx" in qmd and "Trial Balance!" in qmd


def test_every_figure_a_narrative_may_use_has_a_value_or_is_n_a(root: Path) -> None:
    data = collect(root, "acme", "standalone")
    assert set(data["figures"]) == set(FIGURES)
    assert data["figures"]["adjusted_ebitda"]["FY25"] > data["figures"]["reported_ebitda"]["FY25"]
    assert data["figures"]["core_revenue"]["FY26-YTD-SEP"] is None


def test_a_typed_number_is_e602_and_a_bad_reference_is_e874(root: Path) -> None:
    with pytest.raises(NarrativeHasTypedNumbers):
        lint("## Summary\n\nEBITDA grew 12%.\n", "n", FIGURE)
    with pytest.raises(FigureReferenceUnknown, match="no figure made_up"):
        built(root, "## Summary\n\n{{figure:made_up:FY25}}\n")
    with pytest.raises(FigureReferenceUnknown, match="FY30 is not in the engagement"):
        built(root, "## Summary\n\n{{figure:adjusted_ebitda:FY30}}\n")


def test_the_first_run_writes_a_stub_that_names_the_sections(root: Path) -> None:
    result = dd_render.make_diligence_report(run(root), "acme", "standalone", False)
    assert result["status"] == "stub_written"
    text = Path(result["narrative_path"]).read_text(encoding="utf-8")
    assert "{{figure:adjusted_ebitda:FY25}}" in text and "## Working capital" in text
    assert load_config(root).display_unit


def test_the_recast_is_in_the_report_once_it_has_run(root: Path, tmp_path: Path) -> None:
    assert "Recast by period" not in built(root).qmd
    copy = Path(shutil.copytree(root, tmp_path / "data"))
    run_recast(run(copy), "acme", "FY25")
    qmd = built(copy).qmd
    assert "### Recast by period" in qmd and "Service-period recast vs booked revenue" in qmd
    data = collect(copy, "acme", "standalone")
    assert data["figures"]["recast_revenue_effect"]["FY25"] is not None
