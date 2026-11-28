"""`fin report <company> --fdd [--drafts]`: the FDD findings report (docs/fdd.md F8,
templates/report/fdd.qmd). The diligence report's machinery (`fin.report.dd_render.make`) with its
own sections, figures and lint:

- `fdd_narrative.md` (stub on the first run; Claude fills it with `/diligence`), `fdd.html`,
  `fdd.docx`, `fdd.pdf` and `fdd_data.json` in `reports/<company>/<scope>/`.
- The narrative may judge (severity, implications, deal considerations) but never types a number
  (E602; `{{figure:<name>:<period>}}` references, E874) and has no valuation, investment-rating
  or buy/don't-buy language (E972, `fin.report.lint.lint_fdd`).
- Only findings the user marked final are in it, unless `--drafts` (labelled a draft copy).
"""

from typing import Any

from fin.audit.logger import Run
from fin.config import Display
from fin.report import dd_tables as t
from fin.report import fdd_tables as f
from fin.report.dd_data import FIGURES
from fin.report.dd_render import Kind, make
from fin.report.fdd_data import FDD_FIGURES, collect
from fin.report.lint import lint_fdd

SECTIONS = {
    "summary": "Key findings",
    "qoe": "Quality of earnings",
    "nwc": "Working capital",
    "net_debt": "Net debt and debt-like items",
    "revenue": "Revenue, contracts and visibility",
    "walkthroughs": "Walkthroughs",
    "provisions": "Provisions and payroll",
    "tax": "Tax",
    "contingencies": "Contingencies and commitments",
    "related": "Related parties",
    "inquiries": "Management inquiries",
    "basis": "Basis, gates and scope",
}
RULES = """- Findings, severity, implications and deal considerations are allowed. No valuation (no
  enterprise or equity value, multiples), no rating of the company as an investment, no buy or
  don't-buy language (E972). Proposed adjustments and items are not applied; the stub is unaudited
  management accounts; Claude's readings are not verified.
- The key findings are the table fin builds from `fin fdd finding` (final ones only unless
  --drafts); the narrative summarises them."""


def parts(data: dict[str, Any], u: Display) -> tuple[dict[str, str], dict[str, str]]:
    tables = {
        "draft_note": f.draft_note(data),
        "key_findings": f.key_findings(data, u),
        "basis": t.basis_text(data),
        "gates": t.gates_table(data),
        "ebitda": t.ebitda_table(data, u),
        "bridge": t.bridge_table(data, u),
        "adjustments": t.adjustments_table(data, u),
        "walk": t.walk_table(data, u),
        "revenue": t.revenue_table(data, u),
        "nwc_year_end": t.nwc_year_end_table(data, u),
        "nwc_monthly": t.nwc_monthly_table(data, u),
        "net_debt": f.net_debt_table(data, u),
        "coverage": f.coverage_table(data, u),
        "visibility": f.visibility_table(data, u),
        "walkthroughs": f.walkthroughs_table(data, u),
        "receivables": f.receivables_table(data, u),
        "gratuity": f.gratuity_table(data, u),
        "tax_charge": f.tax_table(data, u),
        "gst_returns": f.gst_returns_table(data, u),
        "gst": f.gst_table(data, u),
        "contingencies": f.contingencies_table(data, u),
        "related": f.related_table(data, u),
        "inquiries": f.inquiries_table(data),
        "recon": t.recon_table(data),
        "queries": t.queries_table(data, u),
        "findings": t.findings_table(data),
        "mappings": t.mappings_text(data),
        "sources": t.sources_appendix(data, u),
    }
    return tables, {"recast": t.recast_section(data, u)}


FDD = Kind(
    name="fdd",
    option="--fdd",
    sections=SECTIONS,
    figures={**FIGURES, **FDD_FIGURES},
    rules=RULES,
    collect=collect,
    parts=parts,
    lint=lint_fdd,
)


def make_fdd_report(
    run: Run, company: str, scope: str | None, render_only: bool, drafts: bool
) -> dict[str, Any]:
    """The FDD report of the latest engagement. E873 without one."""
    return make(run, company, scope, render_only, FDD, drafts=drafts)
