"""`fin discrepancies <company> [--level] [--over-threshold] [--json]`: every difference of the
latest reconciliations in one list, ranked by amount, in the display unit (`fin.discrepancies`).
Read-only: a difference is queried (`fin query raise --items`), never fixed."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.discrepancies.rows import discrepancy_rows, format_rows
from fin.discrepancies.view import LEVELS


def _level(value: str | None) -> str | None:
    if value is not None and value not in LEVELS:
        raise typer.BadParameter(f"use one of: {', '.join(LEVELS)}")
    return value


@app.command("discrepancies")
def discrepancies(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    level: Annotated[
        str | None,
        typer.Option(
            "--level", callback=_level, help="Only this level: " + ", ".join(LEVELS) + "."
        ),
    ] = None,
    over: Annotated[
        bool, typer.Option("--over-threshold", help="Only differences at or above the threshold.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Every difference of the latest reconciliations of the open engagement, from the headline
    (trial balance against statements, and reference figures that differ) through accounts and parties to invoices and bank lines,
    each under its parent with what its children explain and what is left unexplained. Ranked by
    amount, in the display unit. Rows that can be queried go to `fin query raise --items`."""

    def body(run: Run) -> None:
        result = discrepancy_rows(run.root, company, level, over)
        if json_output:
            typer.echo(json.dumps(result["rows"], indent=2, default=str))
        else:
            typer.echo(format_rows(company, result))

    execute("discrepancies", {"company": company, "level": level, "over": over}, body)
