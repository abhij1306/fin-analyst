"""`fin report <company>`: narrative stub -> lint (E602) -> render HTML, docx and PDF -> audit row.

Files, in `reports/<company>/<scope>/` in the data folder:
- `narrative.md`: written as a stub on the first run (one `## <section>` heading per section);
  Claude fills it with `/report`.
- `health.html`, `health.docx`, `health.pdf` (Typst): the report.
- `report_data.json`: every value the report used, the narrative references with their values, and
  the data hash.

The report: a summary of key metrics (both scopes where present), one section per metric category
(trend table and a chart), the latest quarters (when quarters are loaded), a peer comparison (with
`--peers`), findings, data coverage, and appendices: condensed statements, metric definitions and
the facts behind the metrics. A section with no data is left out.

Rendering happens in a temporary folder next to them. Only when Quarto has produced all three files
are they moved into place, so a failed render (E601) keeps the last good report.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, ReportRenderFailed
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.report.chart import line_chart
from fin.report.data import (
    CATEGORIES,
    CHARTS,
    TITLES,
    YEARS_SHOWN,
    collect,
    data_hash,
    fact_value,
    to_json,
)
from fin.report.lint import REFERENCE, lint, split_sections, strip_comments
from fin.report.tables import (
    MONEY_UNITS,
    category_rows,
    coverage_tables,
    definitions_table,
    facts_table,
    findings_table,
    is_times,
    label,
    metric_value_text,
    money,
    peers_table,
    quarters_table,
    statements_tables,
    summary_tables,
    trend_table,
)

REPO = Path(__file__).resolve().parents[3]
QMD_FILE = "health.qmd"
TEMPLATE = REPO / "templates" / "report" / QMD_FILE
OUTPUTS = ("health.html", "health.docx", "health.pdf")
DATA_FILE = "report_data.json"
PLACEHOLDER = re.compile(r"\{\{(\w+)(?::(\w+))?\}\}")
QUARTO_TIMEOUT = 600


def report_dir(root: Path, company: str, scope: str) -> Path:
    return root / "reports" / company / scope


@dataclass(frozen=True)
class Built:
    qmd: str  # the filled template, ready for Quarto
    narrative: dict[str, str]  # section key -> narrative with references replaced
    references: dict[str, str]  # reference -> the text it became
    data: dict[str, Any]
    data_hash: str
    charts: dict[str, str]  # file name -> SVG, written next to the qmd


def sections(data: dict[str, Any]) -> list[str]:
    """The narrative sections this report has, in order."""
    keys = ["summary"]
    keys += [key for key, _ in CATEGORIES if category_rows(data, key)]
    if data["quarters"]:
        keys.append("quarters")
    if data["peers"]:
        keys.append("peers")
    return keys + ["findings", "coverage"]


def stub(company: str, scope: str, data: dict[str, Any]) -> str:
    """The narrative stub: instructions in an HTML comment, then one heading per section."""
    metrics = ", ".join(sorted(data["metrics"]))
    accounts = ", ".join(sorted({row["account"] for row in data["facts"]}))
    headings = "\n\n".join(f"## {TITLES[key]}\n" for key in sections(data))
    quarters = ", ".join(data["quarters"]) or "none"
    return f"""<!--
Narrative for the {company} ({scope}) health report. Write a few sentences under each heading, then
run `uv run fin report {company}` to render. The report already has the tables and charts; the
narrative describes what they show.

Rules (checked before rendering):
- Describe, don't judge: no recommendations, ratings, or buy/sell language.
- Never type a number. Write a reference and the renderer fills in the value:
    {{{{metric:<metric>:<period>}}}}   e.g. {{{{metric:ebitda_margin:FY25}}}}
    {{{{fact:<account>:<period>}}}}    e.g. {{{{fact:revenue:FY25}}}}
  Periods are fiscal years (FY25) or quarters (FY26-Q1) and may be written as plain text. Any
  other digit, including list numbers, stops the render with E602. Use bullet lists.
- Keep the headings as they are. Use ### for sub-headings.
- This comment is not rendered.

Years: {", ".join(data["years"])}
Quarters: {quarters}
Metrics: {metrics}
Accounts: {accounts}
-->

{headings}"""


def substitute(text: str, data: dict[str, Any], display_unit: str) -> tuple[str, dict[str, str]]:
    """Replace every reference with its value. Raises E601 for a reference to an unknown metric,
    account or period. A value that is missing (NULL metric, no fact that period) becomes "n/a"."""
    values: dict[str, str] = {}
    problems: list[str] = []
    accounts = {row["account"] for row in data["facts"]}
    for number, line in enumerate(text.splitlines(), start=1):
        for match in REFERENCE.finditer(line):
            kind, name, period = match.groups()
            if period not in data["periods"]:
                problems.append(f"line {number}: {match.group()}: no {period} data")
            elif kind == "metric" and name not in data["metrics"]:
                problems.append(f"line {number}: {match.group()}: no metric {name}")
            elif kind == "fact" and name not in accounts:
                problems.append(f"line {number}: {match.group()}: no account {name}")
            elif kind == "metric":
                value = data["metrics"][name].get(period)
                values[match.group()] = metric_value_text(data, name, value, display_unit)
            else:
                values[match.group()] = money(fact_value(data, name, period), display_unit)
    if problems:
        raise ReportRenderFailed(
            "the narrative has references that cannot be filled:\n"
            + "\n".join(f"  {problem}" for problem in problems)
        )
    return REFERENCE.sub(lambda match: values[match.group()], text), values


def _chart(data: dict[str, Any], category: str, display_unit: str) -> str | None:
    """The category's chart as SVG (its CHARTS metrics with at least two values in the years
    shown), or None."""
    years = data["years"][-YEARS_SHOWN:]
    names = [m for m in CHARTS.get(category, []) if m in data["metrics"]]
    series = [(label(data, m), [data["metrics"][m].get(y) for y in years]) for m in names]
    # A line needs two points.
    series = [(n, values) for n, values in series if sum(v is not None for v in values) > 1]
    if len(years) < 2 or not series:
        return None
    unit = data["metric_info"][names[0]]["unit"]
    per_unit = MONEY_UNITS[display_unit][0] if unit == "rupees" else Decimal(1)
    scale = Decimal(100) if unit == "ratio" and not is_times(data, names[0]) else Decimal(1)
    plotted = [
        (name, [None if v is None else float(v * scale / per_unit) for v in values])
        for name, values in series
    ]
    if unit in ("rupees", "rupees_per_share"):
        prefix, suffix = "₹", MONEY_UNITS[display_unit][1] if unit == "rupees" else ""
    else:
        prefix, suffix = "", {"days": " days"}.get(unit, "%" if scale == 100 else "x")

    def tick(value: float) -> str:
        return f"{'-' if value < 0 else ''}{prefix}{abs(value):,.4g}{suffix}"

    return line_chart(years, plotted, tick)


def _section(*parts: str) -> str:
    """Report blocks joined by blank lines; empty ones (no narrative, no chart) left out."""
    return "\n\n".join(part for part in parts if part)


def category_section(
    data: dict[str, Any], key: str, narrative: str, display_unit: str, charts: dict[str, str]
) -> str:
    """Heading, narrative, trend table and chart of one category."""
    years = data["years"][-YEARS_SHOWN:]
    svg = _chart(data, key, display_unit)
    picture = ""
    if svg is not None:
        name = f"chart-{key}.svg"
        charts[name] = svg
        picture = f"![{TITLES[key]}, {years[0]} to {years[-1]}]({name}){{width=100%}}"
    return _section(
        f"## {TITLES[key]}",
        narrative,
        trend_table(data, category_rows(data, key), years, display_unit),
        picture,
    )


def _narrative_sections(filled: str, data: dict[str, Any]) -> dict[str, str]:
    """The narrative split by section key. Raises when a heading is not a report section, or
    when a section this report does not have has text."""
    texts, unknown = split_sections(filled, TITLES)
    if unknown:
        raise ReportRenderFailed(
            f"narrative headings {', '.join(repr(h) for h in unknown)} are not report sections; "
            f"use: {', '.join(TITLES[key] for key in sections(data))}"
        )
    present = sections(data)
    absent = [TITLES[key] for key in texts if key not in present and texts[key]]
    if absent:
        raise ReportRenderFailed(
            f"the narrative has text under {', '.join(repr(t) for t in absent)}, which this report "
            "does not have (no data for it, or no --peers); remove it or add what it needs"
        )
    return texts


def _blocks(
    data: dict[str, Any], texts: dict[str, str], display_unit: str, charts: dict[str, str]
) -> dict[str, str]:
    """The report's optional sections (categories, quarters, peers) as text; chart SVGs are
    added to `charts`."""
    present = sections(data)
    blocks = {
        "categories": "\n\n".join(
            category_section(data, key, texts.get(key, ""), display_unit, charts)
            for key, _ in CATEGORIES
            if key in present
        ),
    }
    if "quarters" in present:
        blocks["quarters"] = _section(
            f"## {TITLES['quarters']}",
            texts.get("quarters", ""),
            "Quarters loaded from results statements (fiscal year April to March: Q1 ends in "
            "June). Growth is on the same quarter a year before unless it says the previous "
            "quarter; last four quarters (TTM) need four consecutive quarters.",
            quarters_table(data, display_unit),
        )
    if "peers" in present:
        blocks["peers"] = _section(
            f"## {TITLES['peers']}", texts.get("peers", ""), peers_table(data, display_unit)
        )
    return blocks


def build(
    company: str, scope: str, display_unit: str, narrative: str, data: dict[str, Any]
) -> Built:
    """Fill the template with the data and the (already linted) narrative."""
    filled, references = substitute(strip_comments(narrative), data, display_unit)
    texts = _narrative_sections(filled, data)
    charts: dict[str, str] = {}
    blocks = _blocks(data, texts, display_unit, charts)
    tables = {
        "summary": summary_tables(data, display_unit),
        "findings": findings_table(data, display_unit),
        "statements": statements_tables(data, display_unit),
        "definitions": definitions_table(data),
        "facts": facts_table(data, display_unit),
        **coverage_tables(data),
    }
    fields = {
        "title": f"{company}: financial health",
        "subtitle": f"{scope.capitalize()} statements, money in {display_unit}",
    }

    def fill(match: re.Match[str]) -> str:
        kind, key = match.groups()
        if kind == "narrative" and key in TITLES:
            return texts.get(key, "")
        if kind == "table" and key in tables:
            return tables[key]
        if kind == "section" and key in ("categories", "quarters", "peers"):
            return blocks.get(key, "")
        if key is None and kind in fields:
            return fields[kind]
        raise ReportRenderFailed(f"{TEMPLATE} has an unknown placeholder {match.group()}")

    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderFailed(f"cannot read the template {TEMPLATE}: {exc}") from exc
    qmd = PLACEHOLDER.sub(fill, strip_comments(template))
    return Built(qmd, texts, references, data, data_hash(data), charts)


def template_commit() -> str | None:
    """The last commit that changed the template, or None outside a git checkout."""
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        return None
    result = subprocess.run(
        [git, "-C", str(REPO), "log", "-1", "--format=%H", "--", str(TEMPLATE)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def quarto_render(qmd: Path) -> None:
    """Render `qmd` to every format in its front matter, next to it. Raises E001 without Quarto
    and E601 when the render fails."""
    quarto = shutil.which("quarto")
    if quarto is None:
        raise DependencyMissing("quarto is not on PATH; install it with `brew install quarto`")
    try:
        result = subprocess.run(
            [quarto, "render", qmd.name, "--to", "all"],
            cwd=qmd.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=QUARTO_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportRenderFailed(f"quarto render took over {QUARTO_TIMEOUT}s") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-20:])
        raise ReportRenderFailed(f"quarto render exited with {result.returncode}:\n{tail}")
    missing = [name for name in OUTPUTS if not (qmd.parent / name).is_file()]
    if missing:
        raise ReportRenderFailed(f"quarto render did not produce {', '.join(missing)}")


def make_report(
    run: Run,
    company: str,
    scope: str | None,
    render_only: bool,
    peers: list[str] | None = None,
) -> dict[str, Any]:
    """Write the narrative stub if there is none; otherwise lint, render and record the report."""
    root = run.root
    config = load_config(root)
    scope = scope or config.default_scope
    db = company_db_path(root, company)
    if not db.is_file():
        raise ReportRenderFailed(f"no database for {company}; ingest its documents first")
    ensure_database(db, "company")
    folder = report_dir(root, company, scope)
    narrative_path = folder / "narrative.md"
    data = collect(root, company, scope, peers)

    if not narrative_path.is_file():
        if render_only:
            raise ReportRenderFailed(
                f"{narrative_path} does not exist; run `uv run fin report {company}` to write the "
                "stub, then fill it with /report"
            )
        folder.mkdir(parents=True, exist_ok=True)
        narrative_path.write_text(stub(company, scope, data), encoding="utf-8")
        run.event("report", f"wrote narrative stub {narrative_path}", file=str(narrative_path))
        return {
            "company": company,
            "scope": scope,
            "status": "stub_written",
            "narrative_path": str(narrative_path),
            "next": (
                "Fill in each section of the narrative (run /report in Claude Code), then run "
                f"`uv run fin report {company}` again to render. Numbers only as references, "
                "e.g. {{metric:ebitda_margin:FY25}}."
            ),
        }

    narrative = narrative_path.read_text(encoding="utf-8")
    lint(narrative, str(narrative_path))
    built = build(company, scope, config.display_unit, narrative, data)

    temp = folder / f".render-{run.run_id}"
    temp.mkdir(parents=True)
    try:
        (temp / QMD_FILE).write_text(built.qmd, encoding="utf-8")
        for name, svg in built.charts.items():
            (temp / name).write_text(svg, encoding="utf-8")
        report_data = {
            "company": company,
            "scope": scope,
            "display_unit": config.display_unit,
            "data_hash": built.data_hash,
            "references": built.references,
            "data": built.data,
        }
        (temp / DATA_FILE).write_text(to_json(report_data) + "\n", encoding="utf-8")
        quarto_render(temp / QMD_FILE)
        outputs = []
        for name in (*OUTPUTS, DATA_FILE):
            (temp / name).replace(folder / name)
            outputs.append(str(folder / name))
    finally:
        shutil.rmtree(temp)

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
    run.event("report", f"rendered {company} ({scope}) report", context={"outputs": outputs})
    return {
        "company": company,
        "scope": scope,
        "status": "rendered",
        "outputs": outputs,
        "data_hash": built.data_hash,
        "narrative_path": str(narrative_path),
        # References whose value is missing (shown as n/a): flag them.
        "missing_values": sorted(ref for ref, text in built.references.items() if text == "n/a"),
    }
