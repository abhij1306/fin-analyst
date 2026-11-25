"""`fin reconcile <company> [name] [--period]`: run a reconciliation (`fin.diligence.recon`).

D2 has one, `tb_vs_statements`: each audited period's trial balance, rolled up by confirmed ledger
mapping, against its audited standalone statement figures. Lines at or above the engagement
threshold get draft accountant queries; lines below are listed. Nothing is fixed."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import recon

NAMES = (recon.NAME,)


def _name(value: str) -> str:
    if value not in NAMES:
        raise typer.BadParameter(f"use one of: {', '.join(NAMES)}")
    return value


@app.command("reconcile")
def reconcile(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    name: Annotated[
        str, typer.Argument(callback=_name, help="The reconciliation: tb_vs_statements.")
    ] = recon.NAME,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help="Only this annual period, e.g. FY25 (default: every one "
            "with a loaded trial balance).",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Compare two sources for each period, one line per account. Differences at or above the
    threshold become DRAFT accountant queries (fin never approves or sends one); lines below are
    listed; ledgers not mapped to an account are flagged. Differences are queried, never fixed."""

    def body(run: Run) -> None:
        results = recon.tb_vs_statements(run, company, period)
        if json_output:
            typer.echo(json.dumps(results, indent=2, default=str))
        else:
            typer.echo("\n".join(recon.format_result(result) for result in results))

    execute("reconcile", {"company": company, "name": name, "period": period}, body)
