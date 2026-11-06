"""`fin` command-line entry point. Every command runs through the audit logger
(`fin.audit.logger.execute`)."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin import __version__
from fin.audit.errors import EXIT_FAILURES, FileUnreadable, GuardBlocked, ReadOnlyViolation
from fin.audit.logger import Run, execute
from fin.audit.trace import (
    company_history,
    fact_trace,
    format_fact_trace,
    format_history,
    format_metric_trace,
    metric_trace,
)
from fin.config import SCOPES, load_config, write_default_config
from fin.db.migrate import MigrationResult, ensure_current, migrate_all
from fin.delete import delete_company
from fin.doctor import run_checks
from fin.ingest import COMPANY_NAME, files_in, ingest_file
from fin.map.commands import map_app, review_app
from fin.map.dictionary import load_label_map, load_seed
from fin.map.review import write_pending
from fin.metrics.export import Refresh, refresh_analytics
from fin.paths import company_db_path, ensure_data_folder, inbox_dir
from fin.query.evidence import write_evidence
from fin.query.run import query
from fin.query.write import write_sql
from fin.report.render import make_report
from fin.update import update as run_update

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)
app.add_typer(map_app, name="map")
app.add_typer(review_app, name="review")


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


def _report_migrations(run: Run, results: list[MigrationResult]) -> None:
    for result in results:
        if result.from_version == result.to_version:
            line = f"{result.db_file}: up to date at schema version {result.to_version}"
        else:
            backup = result.backup or "none, new file"
            line = (
                f"{result.db_file}: schema version {result.from_version} -> {result.to_version} "
                f"(backup: {backup})"
            )
        typer.echo(line)
        run.event(
            "migrate",
            line,
            file=result.db_file,
            context={
                "from_version": result.from_version,
                "to_version": result.to_version,
                "backup": result.backup,
            },
        )


@app.command()
def init() -> None:
    """Create the data folder, config.toml and databases. Safe to run again; never deletes or
    overwrites."""

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
        _report_migrations(run, migrate_all(run.root, run))
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

    execute("init", {}, body, audit_required=False)


@app.command()
def migrate() -> None:
    """Apply pending database migrations, backing up each file first. Safe to run again."""

    def body(run: Run) -> None:
        _report_migrations(run, migrate_all(run.root, run))

    execute("migrate", {}, body, audit_required=False)


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
                level="info" if check.ok else "error",
                code=check.code,
                context=None if check.fix is None else {"fix": check.fix},
            )
        if json_output:
            payload = {"ok": not failed, "checks": [check.as_dict() for check in checks]}
            typer.echo(json.dumps(payload, indent=2))
        else:
            for check in checks:
                if check.ok:
                    typer.echo(f"ok    {check.name}: {check.detail}")
                else:
                    typer.echo(
                        f"FAIL  {check.name}: {check.detail} [{check.code}] fix: {check.fix}"
                    )
            typer.echo("all checks passed" if not failed else f"{len(failed)} check(s) failed")
        if failed:
            run.failed = len(failed)
            run.exit_code = EXIT_FAILURES

    execute("doctor", {"json": json_output}, body, audit_required=False)


def _company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Load files already ingested again, as a new version."),
]


def _ingest_files(run: Run, jobs: list[tuple[str, list[Path]]], force: bool) -> None:
    """Ingest each company's files, printing progress as `[i/N] company/file: outcome`."""
    config = load_config(run.root)
    total = sum(len(files) for _, files in jobs)
    done = 0
    for company, files in jobs:
        result = ensure_current(run.root, company_db_path(run.root, company), "company", run)
        if result is not None:
            _report_migrations(run, [result])
        label_map = load_label_map(run.root, company)
        for path in files:
            done += 1
            outcome = ingest_file(run, config, company, path, label_map, force=force)
            typer.echo(f"[{done}/{total}] {company}/{path.name}: {outcome}")
        write_pending(run, company)
    if total == 0:
        typer.echo("no files to ingest")
    _echo_refresh(refresh_analytics(run))


def _echo_refresh(refresh: Refresh) -> None:
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
) -> None:
    """Load Excel and PDF documents for one company. Files already loaded are skipped (E105)."""

    def body(run: Run) -> None:
        source = path if path is not None else inbox_dir(run.root, company)
        _ingest_files(run, [(company, files_in(source))], force)

    execute("ingest", {"company": company, "path": path, "force": force}, body)


@app.command("ingest-batch")
def ingest_batch(
    folder: Annotated[
        Path, typer.Argument(help="A folder with one sub-folder per company, e.g. inbox/.")
    ],
    force: ForceOption = False,
) -> None:
    """Load every company sub-folder of FOLDER. Safe to re-run after an interruption: files
    already loaded are skipped (E105), so it resumes where it stopped."""

    def body(run: Run) -> None:
        if not folder.is_dir():
            raise FileUnreadable(f"{folder} is not a folder")
        jobs: list[tuple[str, list[Path]]] = []
        for entry in sorted(folder.iterdir()):
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


@app.command("query")
def query_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    question: Annotated[str, typer.Option("--question", help="The question in words.")],
    sql: Annotated[str, typer.Option("--sql", help="One SELECT statement.")],
) -> None:
    """Answer a question with one read-only SELECT. Prints JSON rows with provenance columns
    (source_file, source_ref, source_confirmed) when the result has fact_id or raw_id. Anything
    other than a single SELECT is rejected (E502)."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(query(run, company, question, sql), indent=2))

    execute("query", {"company": company, "question": question, "sql": sql}, body)


@app.command("evidence")
def evidence_command(
    query_id: Annotated[str, typer.Argument(help="The query_id printed by `fin query`.")],
) -> None:
    """Write an HTML page under evidence/ showing the source of every fact in a query's result.
    A missing source file is flagged on the page (E604, exit 1)."""

    def body(run: Run) -> None:
        path = write_evidence(run, query_id)
        typer.echo(json.dumps({"query_id": query_id, "evidence_path": str(path)}, indent=2))
        if run.failed:
            run.exit_code = EXIT_FAILURES

    execute("evidence", {"query_id": query_id}, body)


@app.command("sql")
def sql_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    sql: Annotated[str, typer.Option("--sql", help="The statement(s) to run.")],
    write: Annotated[
        bool, typer.Option("--write", help="Required: change data (backup + audit log).")
    ] = False,
) -> None:
    """Apply a data change the user asked for: back up the company database, run the SQL in one
    transaction, and record the statement, rows affected and backup in the audit log."""

    def body(run: Run) -> None:
        if not write:
            raise ReadOnlyViolation(
                "`fin sql` changes data only with --write; to read data use `fin query`"
            )
        typer.echo(json.dumps(write_sql(run, company, sql), indent=2))

    execute("sql", {"company": company, "sql": sql, "write": write}, body)


metrics_app = typer.Typer(no_args_is_help=True, help="Metrics and the analytics export.")
app.add_typer(metrics_app, name="metrics")


@metrics_app.command("refresh")
def metrics_refresh() -> None:
    """Rebuild shared/analytics.duckdb from every company's metrics. Also runs after every
    ingest."""
    execute("metrics refresh", {}, lambda run: _echo_refresh(refresh_analytics(run)))


@app.command("audit")
def audit_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    metric: Annotated[
        str | None, typer.Argument(help="Metric to trace, e.g. current_ratio.")
    ] = None,
    fiscal_year: Annotated[str | None, typer.Argument(help="Fiscal year, e.g. FY25.")] = None,
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
    """Update fin: back up all databases, git pull, uv sync, npm ci, fin migrate, fin doctor."""

    def body(run: Run) -> None:
        run_update(run)
        typer.echo("update finished" if not run.failed else "update finished; fin doctor failed")

    execute("update", {}, body)


def _scope(value: str | None) -> str | None:
    if value is not None and value not in SCOPES:
        raise typer.BadParameter(f"use one of: {', '.join(SCOPES)}")
    return value


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
) -> None:
    """Health report. The first run writes reports/<company>/<scope>/narrative.md (fill it with
    /report); the next run lints it (E602 on hand-typed numbers) and renders HTML, docx and PDF.
    A failed render (E601) keeps the last good report."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(make_report(run, company, scope, render), indent=2))

    execute("report", {"company": company, "scope": scope, "render": render}, body)
