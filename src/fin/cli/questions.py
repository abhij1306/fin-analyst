"""`fin schema` (the views for questions) and `fin metric add|list|remove` (saved metrics)."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_optional_company, echo_refresh
from fin.metrics.saved import UNITS, add_metric, list_metrics, remove_metric
from fin.query.guide import QUESTION_VIEWS, describe, format_schema

metric_app = typer.Typer(no_args_is_help=True, help="Saved metrics: add, list and remove.")
app.add_typer(metric_app, name="metric")

CompanyOption = Annotated[
    str | None,
    typer.Option(
        "--company",
        callback=check_optional_company,
        help="Save for this company only; without it, for every company.",
    ),
]


def _check_view(value: str | None) -> str | None:
    if value is not None and value not in QUESTION_VIEWS:
        raise typer.BadParameter(f"use one of: {', '.join(QUESTION_VIEWS)}")
    return value


def _check_unit(value: str) -> str:
    if value not in UNITS:
        raise typer.BadParameter(f"use one of: {', '.join(UNITS)}")
    return value


@app.command("schema")
def schema_command(
    company: Annotated[
        str | None,
        typer.Argument(callback=check_optional_company, help="Company name (optional)."),
    ] = None,
    table: Annotated[
        str | None, typer.Option("--table", callback=_check_view, help="One view only.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """The views meant for questions (current_facts, fact_sources, standard_statements, metrics,
    metric_inputs, metric_definitions, document_facts): columns, types, what each holds and an
    example query. Read it before writing `fin sql`."""

    def body(run: Run) -> None:
        views = describe(run.root, company, table)
        typer.echo(json.dumps(views, indent=2) if json_output else format_schema(views))

    execute("schema", {"company": company, "table": table, "json": json_output}, body)


@metric_app.command("add")
def metric_add(
    name: Annotated[str, typer.Argument(help="Metric name, e.g. cfo_to_revenue.")],
    formula: Annotated[
        str, typer.Option("--formula", help="Accounts and metrics with + - * / ( ).")
    ],
    unit: Annotated[str, typer.Option("--unit", callback=_check_unit, help="ratio|rupees|days.")],
    definition: Annotated[str, typer.Option("--definition", help="What it means, in words.")],
    company: CompanyOption = None,
) -> None:
    """Save a metric built from accounts and metrics. It is computed with the built-in metrics,
    is in metric_definitions with category 'user' and traces to its inputs (metric_inputs)."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(add_metric(run, name, formula, unit, definition, company), indent=2))
        echo_refresh(run)

    args = {"name": name, "formula": formula, "unit": unit, "definition": definition}
    execute("metric add", args | {"company": company}, body)


@metric_app.command("list")
def metric_list(
    company: Annotated[
        str | None,
        typer.Argument(callback=check_optional_company, help="Company (optional)."),
    ] = None,
) -> None:
    """The metrics saved for a company (its own and the shared ones), or the shared ones."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(list_metrics(run.root, company), indent=2, default=str))

    execute("metric list", {"company": company}, body)


@metric_app.command("remove")
def metric_remove(
    name: Annotated[str, typer.Argument(help="The saved metric.")],
    company: CompanyOption = None,
) -> None:
    """Remove a saved metric: the company's with --company, else the one saved for every company.
    Refused while another saved metric uses it."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(remove_metric(run, name, company), indent=2))
        echo_refresh(run)

    execute("metric remove", {"name": name, "company": company}, body)
