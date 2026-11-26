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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
from fin.report.lint import FIGURE, FIGURE_EXAMPLE, lint, split_sections, strip_comments
from fin.report.render import (
    PLACEHOLDER,
    REPO,
    Built,
    _render_files,
    report_dir,
    template_commit,
)
from fin.report.tables import metric_text, money

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


RULES = """- Describe, don't judge: no recommendations, ratings, or buy/sell language. Say what an
  item is and its evidence; the stub is unaudited management accounts; proposed adjustments are
  not applied."""


@dataclass(frozen=True)
class Kind:
    """One report built on this machinery: the diligence report, or the FDD report
    (fin.report.fdd_render). Its files are `<name>.qmd` (template), `<name>_narrative.md`,
    `<name>.html|docx|pdf` and `<name>_data.json`."""

    name: str
    option: str  # the `fin report` option, e.g. --diligence
    sections: dict[str, str]
    figures: dict[str, tuple[str, str, str]]
    rules: str  # the narrative rules the stub states
    collect: Callable[..., dict[str, Any]]  # (root, company, scope, **options)
    # (data, display unit) -> (tables, sections) by placeholder name
    parts: Callable[[dict[str, Any], str], tuple[dict[str, str], dict[str, str]]]
    lint: Callable[[str, str], None]

    @property
    def template(self) -> Path:
        return REPO / "templates" / "report" / f"{self.name}.qmd"

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(f"{self.name}.{ext}" for ext in ("html", "docx", "pdf"))


def stub(company: str, data: dict[str, Any], kind: "Kind | None" = None) -> str:
    """The narrative stub: instructions in an HTML comment, then one heading per section."""
    kind = kind or DILIGENCE
    headings = "\n\n".join(f"## {title}\n" for title in kind.sections.values())
    names = ", ".join(sorted(kind.figures))
    periods = ", ".join(data["periods_shown"])
    return f"""<!--
Narrative for the {company} ({data["scope"]}) {kind.name} report. Write a few sentences under each
heading,
then run `uv run fin report {company} {kind.option}` to render. The report already has the tables;
the narrative
describes what they show.

Rules (checked before rendering):
{kind.rules}
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


def _text(name: str, value: Any, display_unit: str, figures: dict[str, Any]) -> str:
    return (
        metric_text("ratio", value, display_unit)
        if figures[name][1] == "ratio"
        else money(value, display_unit)
    )


def substitute(
    text: str, data: dict[str, Any], display_unit: str, figures: dict[str, Any] = FIGURES
) -> tuple[str, dict[str, str]]:
    """Replace every figure reference with its value. E874 for an unknown figure or a period that is
    not the engagement's; a value that is missing becomes "n/a"."""
    values: dict[str, str] = {}
    problems: list[str] = []
    allowed = set(data["periods_shown"])
    for number, line in enumerate(text.splitlines(), start=1):
        for match in FIGURE.finditer(line):
            _, name, period = match.groups()
            if name not in figures:
                problems.append(f"line {number}: {match.group()}: no figure {name}")
            elif period not in allowed:
                problems.append(
                    f"line {number}: {match.group()}: {period} is not in the engagement"
                )
            else:
                value = data["figures"][name].get(period)
                values[match.group()] = _text(name, value, display_unit, figures)
    if problems:
        raise FigureReferenceUnknown(
            "the narrative has references that cannot be filled:\n"
            + "\n".join(f"  {p}" for p in problems)
        )
    return FIGURE.sub(lambda m: values[m.group()], text), values


def diligence_parts(data: dict[str, Any], u: str) -> tuple[dict[str, str], dict[str, str]]:
    """The diligence report's tables and sections, by placeholder name."""
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
    return tables, sections


def _read_template(kind: Kind) -> str:
    try:
        return kind.template.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderFailed(f"cannot read the template {kind.template}: {exc}") from exc


def build(
    company: str,
    scope: str,
    display_unit: str,
    narrative: str,
    data: dict[str, Any],
    kind: "Kind | None" = None,
) -> Built:
    """Fill the template with the data and the (already linted) narrative."""
    kind = kind or DILIGENCE
    filled, refs = substitute(strip_comments(narrative), data, display_unit, kind.figures)
    texts, unknown = split_sections(filled, kind.sections)
    if unknown:
        raise ReportRenderFailed(
            f"narrative headings {', '.join(repr(h) for h in unknown)} are not report sections; "
            f"use: {', '.join(kind.sections.values())}"
        )
    tables, sections = kind.parts(data, display_unit)
    fields = {
        "title": f"{company}: {'FDD' if kind.name == 'fdd' else kind.name} report",
        "subtitle": f"{scope.capitalize()} statements, money in {display_unit}",
    }

    def fill(match: re.Match[str]) -> str:
        place, key = match.groups()
        if place == "narrative" and key in kind.sections:
            return texts.get(key, "")
        if place == "table" and key in tables:
            return tables[key]
        if place == "section" and key in sections:
            return sections[key]
        if key is None and place in fields:
            return fields[place]
        raise ReportRenderFailed(f"{kind.template} has an unknown placeholder {match.group()}")

    qmd = PLACEHOLDER.sub(fill, strip_comments(_read_template(kind)))
    return Built(qmd, texts, refs, data, data_hash(data), {})


def make_diligence_report(
    run: Run, company: str, scope: str | None, render_only: bool
) -> dict[str, Any]:
    """The diligence report (`fin report <co> --diligence`)."""
    return make(run, company, scope, render_only, DILIGENCE)


def make(
    run: Run, company: str, scope: str | None, render_only: bool, kind: Kind, **options: Any
) -> dict[str, Any]:
    """Write the narrative stub if there is none; otherwise lint, render and record the report.
    `options` go to `kind.collect`."""
    root = run.root
    config = load_config(root)
    scope = scope or config.default_scope
    data = kind.collect(root, company, scope, **options)
    folder = report_dir(root, company, scope)
    narrative_path = folder / f"{kind.name}_narrative.md"
    if not narrative_path.is_file():
        if render_only:
            raise ReportRenderFailed(
                f"{narrative_path} does not exist; run `uv run fin report {company} {kind.option}` "
                "to write the stub, then fill it with /diligence"
            )
        try:
            folder.mkdir(parents=True, exist_ok=True)
            narrative_path.write_text(stub(company, data, kind), encoding="utf-8")
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot write {narrative_path}: {exc}") from exc
        run.event("report", f"wrote {kind.name} narrative stub {narrative_path}")
        return {
            "company": company,
            "scope": scope,
            "status": "stub_written",
            "narrative_path": str(narrative_path),
            "next": (
                "Fill in each section of the narrative (/diligence in Claude Code), then run "
                f"`uv run fin report {company} {kind.option}` again. Numbers only as references, "
                "e.g. {{figure:adjusted_ebitda:FY25}}."
            ),
        }
    try:
        narrative = narrative_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportRenderFailed(f"cannot read {narrative_path}: {exc}") from exc
    kind.lint(narrative, str(narrative_path))
    built = build(company, scope, config.display_unit, narrative, data, kind)
    report_data = {
        "company": company,
        "scope": scope,
        "display_unit": config.display_unit,
        "data_hash": built.data_hash,
        "gates": data["gates"],
        **options,
        "references": built.references,
        "data": built.data,
    }
    outputs = _render_files(
        folder, f".render-{run.run_id}", built, to_json(report_data) + "\n",
        f"{kind.name}.qmd", kind.outputs, f"{kind.name}_data.json",
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
        "report", f"rendered {company} ({scope}) {kind.name} report", context={"outputs": outputs}
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


DILIGENCE = Kind(
    name="diligence",
    option="--diligence",
    sections=SECTIONS,
    figures=FIGURES,
    rules=RULES,
    collect=collect,
    parts=diligence_parts,
    lint=lambda text, name: lint(text, name, FIGURE, FIGURE_EXAMPLE),
)
