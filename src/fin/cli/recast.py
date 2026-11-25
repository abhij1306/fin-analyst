"""`fin recast run|contracts`: the service-period recast (`fin.reconcile.recast`, plan D6).

`run` spreads each sales register line's Amount in Rs over its service period by month, compares
it with what was booked by period and proposes a `recast` adjustment for each period's difference;
a sales invoice without service dates is listed (at or above the threshold it is for Claude to put
in a query: `fin query raise --items`). `contracts` loads a reading of a contract document, as
optional evidence. Nothing is fixed; the user accepts or rejects the adjustments."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.reconcile import contracts
from fin.reconcile.recast_run import format_result, run_recast

recast_app = typer.Typer(
    no_args_is_help=True,
    help="Service-period recast of the sales register, with contracts as optional evidence.",
)
app.add_typer(recast_app, name="recast")
CompanyArg = Annotated[
    str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
]


@recast_app.command("run")
def recast_command(
    company: CompanyArg,
    period: Annotated[
        str | None,
        typer.Option("--period", help="Only this annual period, e.g. FY25 (default: every one)."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Recast each annual period: sales register lines spread straight-line over their service
    dates, compared with revenue booked by invoice date. The difference is proposed as a recast
    adjustment (revenue effect = EBITDA effect). Invoices without service dates are recognised on
    their invoice date and listed."""

    def body(run: Run) -> None:
        results = run_recast(run, company, period)
        if json_output:
            typer.echo(json.dumps(results, indent=2, default=str))
        else:
            typer.echo("\n".join(format_result(result) for result in results))

    execute("recast run", {"company": company, "period": period}, body)


@recast_app.command("contracts")
def contracts_command(
    company: CompanyArg,
    file: Annotated[
        Path | None, typer.Argument(help="The contract document the reading was read from.")
    ] = None,
    reading: Annotated[
        Path | None,
        typer.Option("--reading", help="CSV Claude wrote from the document (file + page)."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Load a reading of a contract document (optional evidence, never the schedule's source), or
    without arguments list the contracts. Whether a Claude-read contract is trusted is the user's
    decision."""
    if (file is None) != (reading is None):
        raise typer.BadParameter("give the contract file and --reading together, or neither")
    if file is not None and (not file.is_file() or reading is None or not reading.is_file()):
        raise typer.BadParameter("the contract file and the reading must be files that exist")

    def body(run: Run) -> None:
        rows = (
            contracts.load(run, company, file, reading)
            if file is not None and reading is not None
            else contracts.list_contracts(run, company)
        )
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(contracts.format_contract, rows)) or "no contracts")

    execute(
        "recast contracts",
        {"company": company, "file": file, "reading": reading},
        body,
    )
