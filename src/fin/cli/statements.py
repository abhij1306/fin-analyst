"""`fin statements` and `fin export`."""

import json
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company, check_scope
from fin.paths import data_dir
from fin.services import reports
from fin.services.result import show
from fin.statements import format_statements, latest_annual_period, statement_rows


@app.command("statements")
def statements_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    period: Annotated[
        str | None,
        typer.Option("--period", help="FY25, FY26-Q1, ... Default: the latest fiscal year."),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope", callback=check_scope, help="standalone or consolidated. Default: both."
        ),
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
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
) -> None:
    """Write reports/<company>/<company>_financials.xlsx: the standard statements, every period
    side by side (one sheet per statement and scope), and a Metrics sheet."""

    show(reports.export_statements(data_dir(), company, via="cli", actor=from_environment()))
