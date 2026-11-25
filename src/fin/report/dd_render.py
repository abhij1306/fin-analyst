"""`fin report <company> --diligence`: the diligence report. Narrative stub -> lint (E602) ->
references
(E874) -> render HTML, docx and PDF -> audit row. Same flow as the health report
(fin.report.render),
for the latest engagement (templates/report/diligence.qmd).

Files, in `reports/<company>/<scope>/` of the data folder:
- `diligence_narrative.md`: written as a stub on the first run; Claude fills it with `/diligence`.
- `diligence.html`, `diligence.docx`, `diligence.pdf`: the report.
- `diligence_data.json`: every value the report used, the narrative references with their values,
  the
  gate states it was rendered with, and the data hash.

The narrative never types a number: `{{figure:<name>:<period>}}` (e.g.
`{{figure:adjusted_ebitda:FY25}}`)
is replaced with the value from the company database. Describe, don't judge.
"""

import re
from typing import Any

from fin.audit.errors import (
    DataFolderNotWritable,
    FigureReferenceUnknown,
    ReportRenderFailed,
)
from fin.audit.logger import Run
from fin.config import load_config
from fin.report import dd_tables as t
from fin.report.data import data_hash, to_json
from fin.report.dd_data import FIGURES, collect
from fin.report.lint import FIGURE, lint, split_sections, strip_comments
from fin.report.render import (
    PLACEHOLDER,
    REPO,
    Built,
    _render_files,
    report_dir,
    template_commit,
)
from fin.report.tables import metric_text, money

QMD_FILE = "diligence.qmd"
TEMPLATE = REPO / "templates" / "report" / QMD_FILE
OUTPUTS = ("diligence.html", "diligence.docx", "diligence.pdf")
DATA_FILE = "diligence_data.json"
NARRATIVE_FILE = "diligence_narrative.md"
SECTIONS = {
    "basis": "Basis and gates",
    "summary": "Summary",
    "ebitda": "Adjusted EBITDA",
    "revenue": "Revenue: core and project revenue",
    "cash": "EBITDA to cash",
    "nwc": "Working capital",
    "recon": "Reconciliations and queries",
    "collections": "Collections and ageing",
    "stub": "Stub: unaudited management accounts",
}


def stub(company: str, data: dict[str, Any]) -> str:
    """The narrative stub: instructions in an HTML comment, then one heading per section."""
    headings = "\n\n".join(f"## {title}\n" for title in SECTIONS.values())
    names = ", ".join(sorted(FIGURES))
    periods = ", ".join(data["periods_shown"])
    return f"""<!--
Narrative for the {company} ({data["scope"]}) diligence report. Write a few sentences under each
heading,
then run `uv run fin report {company} --diligence` to render. The report already has the tables;
the narrative
describes what they show.

Rules (checked before rendering):
- Describe, don't judge: no recommendations, ratings, or buy/sell language. Say what an item is
  and its
  evidence; the stub is unaudited management accounts; proposed adjustments are not applied.
- Never type a number. Write a reference and the renderer fills in the value:
    {{{{figure:<name>:<period>}}}}   e.g. {{{{figure:adjusted_ebitda:FY25}}}}
  Any other digit, including list numbers, stops the render with E602. Use bullet lists.
- Keep the headings as they are. Use ### for sub-headings. Names are tokens (CUST-0001), never
  real names.
- This comment is not rendered.

Periods: {periods} (the stub is written as it is here; only revenue split, working capital and the
recast have stub or no-audit figures).
Figures: {names}
-->

{headings}"""


def _text(name: str, value: Any, unit: str, display_unit: str) -> str:
    return (
        metric_text("ratio", value, display_unit)
        if FIGURES[name][1] == "ratio"
        else money(value, display_unit)
    )


def substitute(text: str, data: dict[str, Any], display_unit: str) -> tuple[str, dict[str, str]]:
    """Replace every figure reference with its value. E874 for an unknown figure or a period that is
    not the engagement's; a value that is missing becomes "n/a"."""
    values: dict[str, str] = {}
    problems: list[str] = []
    allowed = set(data["periods_shown"])
    for number, line in enumerate(text.splitlines(), start=1):
        for match in FIGURE.finditer(line):
            _, name, period = match.groups()
            if name not in FIGURES:
                problems.append(f"line {number}: {match.group()}: no figure {name}")
            elif period not in allowed:
                problems.append(
                    f"line {number}: {match.group()}: {period} is not in the engagement"
                )
            else:
                value = data["figures"][name].get(period)
                values[match.group()] = _text(name, value, display_unit, display_unit)
    if problems:
        raise FigureReferenceUnknown(
            "the narrative has references that cannot be filled:\n"
            + "\n".join(f"  {p}" for p in problems)
        )
    return FIGURE.sub(lambda m: values[m.group()], text), values


def build(
    company: str, scope: str, display_unit: str, narrative: str, data: dict[str, Any]
) -> Built:
    """Fill the template with the data and the (already linted) narrative."""
    filled, refs = substitute(strip_comments(narrative), data, display_unit)
    texts, unknown = split_sections(filled, SECTIONS)
    if unknown:
        raise ReportRenderFailed(
            f"narrative headings {', '.join(repr(h) for h in unknown)} are not report sections; "
            f"use: {', '.join(SECTIONS.values())}"
        )
    u = display_unit
    tables = {
        "basis": t.basis_text(data),
        "gates": t.gates_table(data),
        "ebitda": t.ebitda_table(data, u),
        "bridge": t.bridge_table(data, u),
        "adjustments": t.adjustments_table(data, u),
        "other_income": t.other_income_table(data, u),
        "revenue": t.revenue_table(data, u),
        "walk": t.walk_table(data, u),
        "nwc_year_end": t.nwc_year_end_table(data, u),
        "nwc_monthly": t.nwc_monthly_table(data, u),
        "nwc_excluded": t.nwc_excluded_table(data),
        "recon": t.recon_table(data),
        "queries": t.queries_table(data, u),
        "collections": t.collections_table(data, u),
        "collections_customers": t.collections_customers_table(data, u),
        "stub": t.stub_table(data, u),
        "findings": t.findings_table(data),
        "mappings": t.mappings_text(data),
        "sources": t.sources_appendix(data, u),
    }
    sections = {"customers": t.customers_block(data, u), "recast": t.recast_section(data, u)}
    fields = {
        "title": f"{company}: diligence report",
        "subtitle": f"{scope.capitalize()} statements, money in {display_unit}",
    }

    def fill(match: re.Match[str]) -> str:
        kind, key = match.groups()
        if kind == "narrative" and key in SECTIONS:
            return texts.get(key, "")
        if kind == "table" and key in tables:
            return tables[key]
        if kind == "section" and key in sections:
            return sections[key]
        if key is None and kind in fields:
            return fields[kind]
        raise ReportRenderFailed(f"{TEMPLATE} has an unknown placeholder {match.group()}")

    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderFailed(f"cannot read the template {TEMPLATE}: {exc}") from exc
    qmd = PLACEHOLDER.sub(fill, strip_comments(template))
    return Built(qmd, texts, refs, data, data_hash(data), {})


def make_diligence_report(
    run: Run, company: str, scope: str | None, render_only: bool
) -> dict[str, Any]:
    """Write the narrative stub if there is none; otherwise lint, render and record the report."""
    root = run.root
    config = load_config(root)
    scope = scope or config.default_scope
    data = collect(root, company, scope)
    folder = report_dir(root, company, scope)
    narrative_path = folder / NARRATIVE_FILE
    if not narrative_path.is_file():
        if render_only:
            raise ReportRenderFailed(
                f"{narrative_path} does not exist; run `uv run fin report {company} --diligence` "
                "to write the stub, then fill it with /diligence"
            )
        try:
            folder.mkdir(parents=True, exist_ok=True)
            narrative_path.write_text(stub(company, data), encoding="utf-8")
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot write {narrative_path}: {exc}") from exc
        run.event("report", f"wrote diligence narrative stub {narrative_path}")
        return {
            "company": company,
            "scope": scope,
            "status": "stub_written",
            "narrative_path": str(narrative_path),
            "next": (
                "Fill in each section of the narrative (/diligence in Claude Code), then run "
                f"`uv run fin report {company} --diligence` again. Numbers only as references, "
                "e.g. {{figure:adjusted_ebitda:FY25}}."
            ),
        }
    try:
        narrative = narrative_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportRenderFailed(f"cannot read {narrative_path}: {exc}") from exc
    lint(narrative, str(narrative_path), FIGURE)
    built = build(company, scope, config.display_unit, narrative, data)
    report_data = {
        "company": company,
        "scope": scope,
        "display_unit": config.display_unit,
        "data_hash": built.data_hash,
        "gates": data["gates"],
        "references": built.references,
        "data": built.data,
    }
    outputs = _render_files(
        folder, f".render-{run.run_id}", built, to_json(report_data) + "\n",
        QMD_FILE, OUTPUTS, DATA_FILE,
    )  # fmt: skip
    run.record(
        "reports",
        {
            "company": company,
            "output_paths": to_json(outputs),
            "template_commit": template_commit(),
            "data_hash": built.data_hash,
            "narrative_path": str(narrative_path),
        },
    )
    run.event(
        "report", f"rendered {company} ({scope}) diligence report", context={"outputs": outputs}
    )
    return {
        "company": company,
        "scope": scope,
        "status": "rendered",
        "outputs": outputs,
        "data_hash": built.data_hash,
        "gates": {g["gate"]: g["state"] for g in data["gates"]},
        "narrative_path": str(narrative_path),
        "missing_values": sorted(ref for ref, text in built.references.items() if text == "n/a"),
    }
