"""`fin sql`, `fin dashboard`, `fin audit`, `fin delete`, `fin update` and `fin report`."""

import json
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.errors import EXIT_FAILURES, GuardBlocked
from fin.audit.logger import Run, execute
from fin.audit.trace import (
    company_history,
    fact_trace,
    format_fact_trace,
    format_history,
    format_metric_trace,
    metric_trace,
)
from fin.cli.core import app, check_company, check_scope
from fin.dashboard import serve as serve_dashboard
from fin.delete import delete_company
from fin.paths import data_dir
from fin.query.run import query
from fin.query.write import write_sql
from fin.services import reports, system
from fin.services.result import show


@app.command("sql")
def sql_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
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

    if build:
        show(system.build_dashboard(data_dir(), via="cli", actor=from_environment()))
        return
    execute("dashboard", {"build": False}, lambda run: serve_dashboard(run.root))


@app.command("audit")
def audit_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
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

    # A usage error, before the run starts (like the option callbacks).
    if (metric is None) != (fiscal_year is None):
        raise typer.BadParameter("give both METRIC and FISCAL_YEAR, or neither")

    def body(run: Run) -> None:
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
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
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

    show(system.update_fin(data_dir(), via="cli", actor=from_environment()))


@app.command("report")
def report_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    scope: Annotated[
        str | None,
        typer.Option("--scope", callback=check_scope, help="standalone or consolidated."),
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
    diligence: Annotated[
        bool,
        typer.Option(
            "--diligence",
            help="The diligence report of the latest engagement (adjusted EBITDA, working "
            "capital, reconciliations, collections, customers, the recast; the narrative is "
            "diligence_narrative.md, with {{figure:name:period}} references).",
        ),
    ] = False,
    fdd: Annotated[
        bool,
        typer.Option(
            "--fdd",
            help="The FDD findings report of the latest engagement (final findings by severity "
            "and every FDD section; the narrative is fdd_narrative.md).",
        ),
    ] = False,
    drafts: Annotated[
        bool, typer.Option("--drafts", help="With --fdd: include draft findings (a draft copy).")
    ] = False,
) -> None:
    """Health report. The first run writes reports/<company>/<scope>/narrative.md (fill it with
    /report); the next run lints it (E602 on hand-typed numbers) and renders HTML, docx and PDF.
    A failed render (E601) keeps the last good report."""
    peer_list = [check_company(p.strip()) for p in peers.split(",") if p.strip()] if peers else None
    if diligence and fdd:
        raise typer.BadParameter("use --diligence or --fdd, not both")

    kind = "fdd" if fdd else "diligence" if diligence else "health"
    given = {"scope": scope, "render": render, "peers": peer_list, "drafts": drafts}

    def body(run: Run) -> None:
        typer.echo(json.dumps(reports.make(run, company, kind, **given), indent=2))

    execute("report", reports.report_args(company, kind, **given), body)
