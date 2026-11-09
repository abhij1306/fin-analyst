"""`fin` command-line entry point. Every command runs through the audit logger
(`fin.audit.logger.execute`)."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin import __version__
from fin.audit.errors import EXIT_FAILURES, FileUnreadable, GuardBlocked
from fin.audit.logger import Run, execute
from fin.audit.trace import (
    company_history,
    fact_trace,
    format_fact_trace,
    format_history,
    format_metric_trace,
    metric_trace,
)
from fin.config import SCOPES, load_config, set_docling, write_default_config
from fin.dashboard import build as build_dashboard
from fin.dashboard import serve as serve_dashboard
from fin.db.schema import ensure_database
from fin.delete import delete_company
from fin.diligence.cli import adjust_app, engagement_app, gate_app, query_app
from fin.doctor import Check, run_checks
from fin.findings import accept, format_finding, list_findings
from fin.ingest import files_in, ingest_file
from fin.map.commands import map_app, review_app
from fin.map.dictionary import load_label_map, load_seed
from fin.map.review import write_pending
from fin.metrics.export import refresh_analytics
from fin.paths import (
    COMPANY_NAME,
    analytics_db_path,
    audit_db_path,
    company_db_path,
    dictionary_db_path,
    ensure_data_folder,
    inbox_dir,
)
from fin.query.run import query
from fin.query.write import write_sql
from fin.report.render import make_report
from fin.statements import format_statements, latest_annual_period, statement_rows
from fin.status import companies, format_status, status
from fin.update import update as run_update
from fin.verify import format_result, verify
from fin.workbook import export_workbook

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)
app.add_typer(map_app, name="map")
app.add_typer(review_app, name="review")
app.add_typer(engagement_app, name="engagement")
app.add_typer(query_app, name="query")
app.add_typer(adjust_app, name="adjust")
app.add_typer(gate_app, name="gate")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fin {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local financial health analysis."""


def _create_shared_databases(run: Run) -> None:
    for kind, path in (
        ("audit", audit_db_path(run.root)),
        ("dictionary", dictionary_db_path(run.root)),
        ("analytics", analytics_db_path(run.root)),
    ):
        created = ensure_database(path, kind)
        rel = path.relative_to(run.root).as_posix()
        line = f"{'created ' if created else 'kept    '} {rel}"
        typer.echo(line)
        run.event("database", line, file=rel, context={"created": created})


@app.command()
def init(
    docling: Annotated[
        bool | None,
        typer.Option(
            "--docling/--no-docling",
            help="Record whether the optional Docling PDF add-on is installed ([pdf] docling in "
            "config.toml; install.sh sets it). Left as it is when not given.",
        ),
    ] = None,
) -> None:
    """Create the data folder, config.toml and databases. Safe to run again; never deletes or
    overwrites (`--docling` / `--no-docling` changes only `[pdf] docling` in config.toml)."""

    def body(run: Run) -> None:
        created, existing = ensure_data_folder(run.root)
        for folder in created:
            typer.echo(f"created  {folder}")
        run.event(
            "folders",
            f"{len(created)} created, {len(existing)} already present",
            context={"created": [str(folder) for folder in created]},
        )
        if write_default_config(run.root):
            typer.echo("created  config.toml")
            run.event("config", "wrote default config.toml")
        else:
            typer.echo("kept     config.toml (already present, not changed)")
            run.event("config", "config.toml already present, not changed")
        if docling is not None:
            set_docling(run.root, docling)
            line = f"set      [pdf] docling = {str(docling).lower()} in config.toml"
            typer.echo(line)
            run.event("config", line)
        _create_shared_databases(run)
        seed = load_seed(run.root, run)
        line = (
            f"dictionary: {seed.accounts_added} accounts and {seed.labels_added} label mappings "
            "added from templates/dictionary"
        )
        typer.echo(line)
        run.event("dictionary", line)
        typer.echo(
            f"data folder {run.root}: {len(created)} created, {len(existing)} already present"
        )

    execute("init", {"docling": docling}, body, audit_required=False)


def _check_level(check: Check) -> str:
    """Log level for a doctor check: error when it failed, warning when it warns, else info."""
    if not check.ok:
        return "error"
    if check.warning:
        return "warning"
    return "info"


def _check_line(check: Check) -> str:
    """One doctor check as a line of plain text."""
    if check.warning:
        return f"warn  {check.name}: {check.detail}; fix: {check.fix}"
    if check.ok:
        return f"ok    {check.name}: {check.detail}"
    return f"FAIL  {check.name}: {check.detail} [{check.code}] fix: {check.fix}"


def _print_checks(checks: list[Check], failed: int, json_output: bool) -> None:
    """Print the doctor results as JSON or as plain text."""
    if json_output:
        payload = {"ok": not failed, "checks": [check.as_dict() for check in checks]}
        typer.echo(json.dumps(payload, indent=2))
        return
    for check in checks:
        typer.echo(_check_line(check))
    typer.echo("all checks passed" if not failed else f"{failed} check(s) failed")


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Check dependencies, versions, the data folder, config.toml and the audit schema."""

    def body(run: Run) -> None:
        checks = run_checks(run.root)
        failed = [check for check in checks if not check.ok]
        for check in checks:
            run.event(
                "check",
                f"{check.name}: {check.detail}",
                level=_check_level(check),
                code=check.code,
                context=None if check.fix is None else {"fix": check.fix},
            )
        _print_checks(checks, len(failed), json_output)
        if failed:
            run.failed = len(failed)
            run.exit_code = EXIT_FAILURES

    execute("doctor", {"json": json_output}, body, audit_required=False)


def _company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


def _optional_company(value: str | None) -> str | None:
    return None if value is None else _company(value)


def _scope(value: str | None) -> str | None:
    if value is not None and value not in SCOPES:
        raise typer.BadParameter(f"use one of: {', '.join(SCOPES)}")
    return value


ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Load files already ingested again, as a new version."),
]


def _ingest_files(
    run: Run, jobs: list[tuple[str, list[Path]]], force: bool, reading: Path | None = None
) -> None:
    """Ingest each company's files, printing progress as `[i/N] company/file: outcome`."""
    config = load_config(run.root)
    total = sum(len(files) for _, files in jobs)
    done = 0
    for company, files in jobs:
        ensure_database(company_db_path(run.root, company), "company")
        label_map = load_label_map(run.root, company)
        for path in files:
            done += 1
            outcome = ingest_file(
                run, config, company, path, label_map, force=force, reading=reading
            )
            typer.echo(f"[{done}/{total}] {company}/{path.name}: {outcome}")
        write_pending(run, company)
    if total == 0:
        typer.echo("no files to ingest")
    _echo_refresh(run)


def _echo_refresh(run: Run) -> None:
    refresh = refresh_analytics(run)
    typer.echo(
        f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
        f"{refresh.metric_rows} metric rows"
    )


@app.command()
def ingest(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    path: Annotated[
        Path | None,
        typer.Argument(help="A file or folder. Default: inbox/<company>/ in the data folder."),
    ] = None,
    force: ForceOption = False,
    reading: Annotated[
        Path | None,
        typer.Option(
            "--reading",
            help="A reading file (CSV of figures Claude read from PATH's pages or cells): loaded "
            "for the statements fin cannot read. PATH must be one file.",
        ),
    ] = None,
) -> None:
    """Load Excel and PDF documents for one company. Files already loaded are skipped (E105)."""

    def body(run: Run) -> None:
        if reading is not None and (path is None or not path.is_file()):
            raise typer.BadParameter("--reading needs PATH to be the one file it was read from")
        source = path if path is not None else inbox_dir(run.root, company)
        _ingest_files(run, [(company, files_in(source))], force, reading)

    args = {"company": company, "path": path, "force": force, "reading": reading}
    execute("ingest", args, body)


@app.command("ingest-batch")
def ingest_batch(
    folder: Annotated[
        Path | None,
        typer.Argument(help="A folder with one sub-folder per company. Default: inbox/."),
    ] = None,
    force: ForceOption = False,
) -> None:
    """Load every company sub-folder of FOLDER. Safe to re-run after an interruption: files
    already loaded are skipped (E105), so it resumes where it stopped."""

    def body(run: Run) -> None:
        source = folder if folder is not None else run.root / "inbox"
        if not source.is_dir():
            raise FileUnreadable(f"{source} is not a folder")
        jobs: list[tuple[str, list[Path]]] = []
        for entry in sorted(source.iterdir()):
            if entry.is_dir() and COMPANY_NAME.match(entry.name):
                jobs.append((entry.name, files_in(entry)))
            elif not entry.name.startswith("."):
                run.skipped += 1
                message = (
                    f"{entry.name}: skipped; put files in a company folder named with lower-case "
                    "letters, digits, '-' and '_'"
                )
                run.event("ingest", message, level="warning", file=entry.name)
                typer.echo(message)
        _ingest_files(run, jobs, force)

    execute("ingest-batch", {"folder": folder, "force": force}, body)


@app.command("verify")
def verify_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    path: Annotated[Path, typer.Argument(help="The original document the reading was read from.")],
    reading: Annotated[
        Path, typer.Option("--reading", help="An independent reading of the document (CSV).")
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Compare an independent reading with what is loaded for a document: agree, differ,
    missing in fin, missing in reading. Lines that do not agree are findings (shown, and recorded
    in the audit log), not errors. Agreeing facts are marked verified (a fact read by Claude only by
    a reading other than the one it was loaded from); no figure is changed."""

    def body(run: Run) -> None:
        result = verify(run, company, path, reading)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(format_result(result))

    execute(
        "verify",
        {"company": company, "path": path, "reading": reading, "json": json_output},
        body,
    )


findings_app = typer.Typer(
    no_args_is_help=True,
    help="Findings: figures in what a company filed that do not add up. Show them; save as is.",
)
app.add_typer(findings_app, name="findings")


@findings_app.command("list")
def findings_list(
    company: Annotated[
        str | None, typer.Argument(callback=_optional_company, help="Only this company.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Open findings, then accepted ones: this document's figure and what it was compared with,
    each with its file and page or cell. A document with open findings is held: not in current
    figures and metrics."""

    def body(run: Run) -> None:
        names = [company] if company is not None else companies(run.root)
        rows = [row for name in names for row in list_findings(run, name)]
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(format_finding, rows)) if rows else "no findings")

    execute("findings list", {"company": company, "json": json_output}, body)


@findings_app.command("accept")
def findings_accept(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    ids: Annotated[list[int], typer.Argument(help="Finding ids from `fin findings list`.")],
) -> None:
    """Save as is: accept open findings. A held document with no open findings left is loaded
    exactly as filed; its findings stay attached, marked accepted."""

    def body(run: Run) -> None:
        for doc in accept(run, company, ids):
            typer.echo(
                f"{company}/{doc['file']} (doc_id {doc['doc_id']}): {doc['status']}, "
                f"{doc['open']} open findings left"
            )
        _echo_refresh(run)

    execute("findings accept", {"company": company, "ids": ids}, body)


@app.command("status")
def status_command(
    company: Annotated[
        str | None, typer.Argument(callback=_optional_company, help="Only this company.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Per company: periods loaded and gaps, held documents, open and accepted findings, mappings
    waiting for review, unverified figures read by Claude, inbox files not yet ingested, and an
    open diligence engagement's queries, proposed adjustments and gates."""

    def body(run: Run) -> None:
        result = status(run, company)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(format_status(result))

    execute("status", {"company": company, "json": json_output}, body)


@app.command("statements")
def statements_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    period: Annotated[
        str | None,
        typer.Option("--period", help="FY25, FY26-Q1, ... Default: the latest fiscal year."),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option("--scope", callback=_scope, help="standalone or consolidated. Default: both."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON rows.")] = False,
) -> None:
    """Show the profit and loss, balance sheet and cash flow in the standard account order, with
    an "Unmapped / other" line wherever a printed subtotal is not explained by its mapped lines."""

    def body(run: Run) -> None:
        rows = statement_rows(run.root, company, period, scope)
        if period is None:
            latest = latest_annual_period(rows)
            rows = [row for row in rows if row["period"] == latest]
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo(format_statements(company, rows))

    execute("statements", {"company": company, "period": period, "scope": scope}, body)


@app.command("export")
def export_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
) -> None:
    """Write reports/<company>/<company>_financials.xlsx: the standard statements, every period
    side by side (one sheet per statement and scope), and a Metrics sheet."""

    def body(run: Run) -> None:
        path = export_workbook(run.root, company)
        typer.echo(f"exported {path.relative_to(run.root).as_posix()}")

    execute("export", {"company": company}, body)


@app.command("sql")
def sql_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    sql: Annotated[str, typer.Option("--sql", help="One SELECT, or with --write the change.")],
    question: Annotated[
        str | None, typer.Option("--question", help="The question in words (recorded).")
    ] = None,
    evidence: Annotated[
        bool,
        typer.Option("--evidence", help="Also write evidence/<query_id>.html with every source."),
    ] = False,
    write: Annotated[
        bool, typer.Option("--write", help="Change data the user asked to change (backup + log).")
    ] = False,
) -> None:
    """Read: run one SELECT and print JSON rows, with provenance columns (source_file,
    source_ref, source_confirmed, ...) when the result has fact_ids (every line of a summed
    value), fact_id or raw_id. --evidence writes a
    page showing the source of every fact (a missing source is flagged, E604, exit 1). Anything but
    one SELECT is rejected (E502) unless --write is given. With --write: back up the company
    database, run the SQL in one transaction, and log the statement, rows affected and backup."""

    def body(run: Run) -> None:
        if write:
            result = write_sql(run, company, sql, question)
        else:
            result = query(run, company, question, sql, evidence)
        typer.echo(json.dumps(result, indent=2))
        if run.failed:
            run.exit_code = EXIT_FAILURES

    args = {"company": company, "sql": sql, "question": question, "evidence": evidence}
    execute("sql", args | {"write": write}, body)


@app.command()
def dashboard(
    build: Annotated[
        bool, typer.Option("--build", help="Make a static build instead of starting the server.")
    ] = False,
) -> None:
    """Refresh the dashboard data, then start the Evidence dev server (or --build a static site)."""

    def body(run: Run) -> None:
        if build:
            typer.echo(f"dashboard built: {build_dashboard(run.root)}")
        else:
            serve_dashboard(run.root)

    execute("dashboard", {"build": build}, body)


@app.command("audit")
def audit_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    metric: Annotated[
        str | None, typer.Argument(help="Metric to trace, e.g. current_ratio.")
    ] = None,
    fiscal_year: Annotated[
        str | None, typer.Argument(help="Period: a fiscal year (FY25) or a quarter (FY26-Q1).")
    ] = None,
    fact: Annotated[int | None, typer.Option("--fact", help="Trace one fact_id.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Trace a figure to its file and cell or page (`--fact ID`, or METRIC FISCAL_YEAR), or with
    neither, list the company's history: ingests, reviews, queries, writes, reports, failures."""

    def body(run: Run) -> None:
        if (metric is None) != (fiscal_year is None):
            raise typer.BadParameter("give both METRIC and FISCAL_YEAR, or neither")
        if fact is not None:
            result, text = fact_trace(run.root, company, fact), format_fact_trace
        elif metric is not None and fiscal_year is not None:
            result, text = (
                metric_trace(run.root, company, metric, fiscal_year),
                format_metric_trace,
            )
        else:
            result, text = company_history(run.root, company), format_history
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(text(result))

    execute(
        "audit",
        {"company": company, "metric": metric, "fiscal_year": fiscal_year, "fact": fact},
        body,
    )


@app.command("delete")
def delete_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the typed confirmation (when the user asked).")
    ] = False,
) -> None:
    """Delete a company's database and files after backing them up to backups/<run_id>/. The audit
    trail stays. Asks you to type the company name unless --yes is given."""

    def body(run: Run) -> None:
        if not yes:
            try:
                typed = typer.prompt(
                    f"This deletes all data of {company}. Type its name to confirm"
                )
            except typer.Abort as exc:
                raise GuardBlocked("not confirmed; nothing deleted (use --yes to skip)") from exc
            if typed.strip() != company:
                raise GuardBlocked("the name typed does not match; nothing deleted")
        typer.echo(json.dumps(delete_company(run, company), indent=2))

    execute("delete", {"company": company, "yes": yes}, body)


@app.command("update")
def update_command() -> None:
    """Update fin: back up databases, git pull, uv sync, npm ci, refresh metrics, run fin doctor."""

    def body(run: Run) -> None:
        run_update(run)
        typer.echo("update finished" if not run.failed else "update finished; fin doctor failed")

    execute("update", {}, body)


@app.command("report")
def report_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    scope: Annotated[
        str | None,
        typer.Option("--scope", callback=_scope, help="standalone or consolidated."),
    ] = None,
    render: Annotated[
        bool,
        typer.Option("--render", help="Only render: fail (E601) if there is no narrative yet."),
    ] = False,
    peers: Annotated[
        str | None,
        typer.Option(
            "--peers",
            help="Other companies to compare with, comma-separated (e.g. bluepeak,tarang): a "
            "table of key metrics for the latest fiscal year all of them have.",
        ),
    ] = None,
) -> None:
    """Health report. The first run writes reports/<company>/<scope>/narrative.md (fill it with
    /report); the next run lints it (E602 on hand-typed numbers) and renders HTML, docx and PDF.
    A failed render (E601) keeps the last good report."""
    peer_list = [_company(p.strip()) for p in peers.split(",") if p.strip()] if peers else None

    def body(run: Run) -> None:
        result = make_report(run, company, scope, render, peer_list)
        typer.echo(json.dumps(result, indent=2))

    args = {"company": company, "scope": scope, "render": render, "peers": peer_list}
    execute("report", args, body)
