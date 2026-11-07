"""`fin report <company>`: narrative stub -> lint (E602) -> render HTML, docx and PDF -> audit row.

Files, in `reports/<company>/<scope>/` in the data folder:
- `narrative.md`: written as a stub on the first run (one `## <section>` heading per section);
  Claude fills it with `/report`.
- `health.html`, `health.docx`, `health.pdf` (Typst): the report.
- `report_data.json`: every value the report used, the narrative references with their values, and
  the data hash.

Rendering happens in a temporary folder next to them. Only when Quarto has produced all three files
are they moved into place, so a failed render (E601) keeps the last good report.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, ReportRenderFailed
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from fin.report.data import (
    SECTIONS,
    TITLES,
    collect,
    data_hash,
    data_quality_tables,
    fact_value,
    metric_text,
    money,
    section_table,
    to_json,
)
from fin.report.lint import REFERENCE, lint, split_sections, strip_comments

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "templates" / "report" / "health.qmd"
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


def stub(company: str, scope: str, data: dict[str, Any]) -> str:
    """The narrative stub: instructions in an HTML comment, then one heading per section."""
    metrics = ", ".join(sorted(data["metrics"]))
    accounts = ", ".join(sorted({row["account"] for row in data["facts"]}))
    headings = "\n\n".join(f"## {title}\n" for _, title, _ in SECTIONS)
    return f"""<!--
Narrative for the {company} ({scope}) health report. Write a few sentences under each heading, then
run `uv run fin report {company}` to render. The report already has the tables; the narrative
describes what they show.

Rules (checked before rendering):
- Describe, don't judge: no recommendations, ratings, or buy/sell language.
- Never type a number. Write a reference and the renderer fills in the value:
    {{{{metric:<metric>:<FY>}}}}   e.g. {{{{metric:ebitda_margin:FY25}}}}
    {{{{fact:<account>:<FY>}}}}    e.g. {{{{fact:revenue:FY25}}}}
  Fiscal years may be written as plain text (FY25). Any other digit, including list numbers,
  stops the render with E602. Use bullet lists.
- Keep the headings as they are. Use ### for sub-headings.
- This comment is not rendered.

Years: {", ".join(data["years"])}
Metrics: {metrics}
Accounts: {accounts}
-->

{headings}"""


def substitute(text: str, data: dict[str, Any], display_unit: str) -> tuple[str, dict[str, str]]:
    """Replace every reference with its value. Raises E601 for a reference to an unknown metric,
    account or year. A value that is missing (NULL metric, no fact that year) becomes "n/a"."""
    values: dict[str, str] = {}
    problems: list[str] = []
    accounts = {row["account"] for row in data["facts"]}
    for number, line in enumerate(text.splitlines(), start=1):
        for match in REFERENCE.finditer(line):
            kind, name, year = match.groups()
            if year not in data["years"]:
                problems.append(f"line {number}: {match.group()}: no {year} data")
            elif kind == "metric" and name not in data["metrics"]:
                problems.append(f"line {number}: {match.group()}: no metric {name}")
            elif kind == "fact" and name not in accounts:
                problems.append(f"line {number}: {match.group()}: no account {name}")
            elif kind == "metric":
                unit = data["metric_units"][name]
                values[match.group()] = metric_text(
                    name, unit, data["metrics"][name][year], display_unit
                )
            else:
                values[match.group()] = money(fact_value(data, name, year), display_unit)
    if problems:
        raise ReportRenderFailed(
            "the narrative has references that cannot be filled:\n"
            + "\n".join(f"  {problem}" for problem in problems)
        )
    return REFERENCE.sub(lambda match: values[match.group()], text), values


def build(
    company: str, scope: str, display_unit: str, narrative: str, data: dict[str, Any]
) -> Built:
    """Fill the template with the data and the (already linted) narrative."""
    filled, references = substitute(strip_comments(narrative), data, display_unit)
    sections, unknown = split_sections(filled, TITLES)
    if unknown:
        raise ReportRenderFailed(
            f"narrative headings {', '.join(repr(h) for h in unknown)} are not report sections; "
            f"use: {', '.join(TITLES.values())}"
        )
    tables = {key: section_table(data, rows, display_unit) for key, _, rows in SECTIONS if rows}
    tables |= data_quality_tables(data, display_unit)
    fields = {
        "title": f"{company}: financial health",
        "subtitle": f"{scope.capitalize()} statements, money in {display_unit}",
    }

    def fill(match: re.Match[str]) -> str:
        kind, key = match.groups()
        if kind == "narrative" and key in TITLES:
            return sections.get(key, "")
        if kind == "table" and key in tables:
            return tables[key]
        if key is None and kind in fields:
            return fields[kind]
        raise ReportRenderFailed(f"{TEMPLATE} has an unknown placeholder {match.group()}")

    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderFailed(f"cannot read the template {TEMPLATE}: {exc}") from exc
    qmd = PLACEHOLDER.sub(fill, strip_comments(template))
    return Built(qmd, sections, references, data, data_hash(data))


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


def make_report(run: Run, company: str, scope: str | None, render_only: bool) -> dict[str, Any]:
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
    data = collect(root, company, scope)

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
        (temp / "health.qmd").write_text(built.qmd, encoding="utf-8")
        report_data = {
            "company": company,
            "scope": scope,
            "display_unit": config.display_unit,
            "data_hash": built.data_hash,
            "references": built.references,
            "data": built.data,
        }
        (temp / DATA_FILE).write_text(to_json(report_data) + "\n", encoding="utf-8")
        quarto_render(temp / "health.qmd")
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
