"""`fin reconcile <company> [name] [--period]`: run reconciliations (`fin.diligence.recon`,
`fin.reconcile`).

tb_vs_statements: each audited period's trial balance, rolled up by confirmed ledger mapping,
against its audited standalone statement figures; lines at or above the engagement threshold get
draft accountant queries. D4 adds sales_register_vs_gl, purchase_register_vs_gl, gl_vs_tb and
payroll_vs_gl: their lines at or above the threshold are grouped into queries by Claude
(`fin query raise --items`); fx lines are listed, not queried. Nothing is fixed."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import recon
from fin.reconcile import NAMES, RECONCILIATIONS, run_all, run_new
from fin.reconcile.format import format_result


def _name(value: str | None) -> str | None:
    if value is not None and value not in NAMES:
        raise typer.BadParameter(f"use one of: {', '.join(NAMES)}")
    return value


def _text(result: dict) -> str:  # type: ignore[type-arg]
    return recon.format_result(result) if result["name"] == recon.NAME else format_result(result)


@app.command("reconcile")
def reconcile(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    name: Annotated[
        str | None,
        typer.Argument(
            callback=_name,
            help="One reconciliation: " + ", ".join(NAMES) + ". Default: every one whose inputs "
            "are loaded.",
        ),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help="Only this period, e.g. FY25 (default: every period the reconciliation applies "
            "to).",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Compare two sources for each period. tb_vs_statements: one line per account, differences at
    or above the threshold become DRAFT queries. The others (registers vs Ledger Vouchers, Ledger
    Vouchers vs trial balance, payroll vs Ledger Vouchers): lines at or above the threshold are for
    Claude to group into queries (`fin query raise --items`); fx lines are listed, not queried.
    Differences are queried, never fixed."""

    def body(run: Run) -> None:
        if name is None:
            results = run_all(run, company, period)
        elif name in RECONCILIATIONS:
            results = run_new(run, company, name, period)
        else:
            results = recon.tb_vs_statements(run, company, period)
        if json_output:
            typer.echo(json.dumps(results, indent=2, default=str))
        else:
            typer.echo("\n".join(_text(result) for result in results))

    execute("reconcile", {"company": company, "name": name, "period": period}, body)
