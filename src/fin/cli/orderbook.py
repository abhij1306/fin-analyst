"""`fin visibility <company>`: contracted backlog and revenue visibility (docs/fdd.md F7).

For the 12 months after the last annual period: the backlog and the revenue each source covers
(unbilled orderbook, billed but not yet earned, customer contracts), against the last period's
core revenue (`fin.reconcile.visibility`). The orderbook loads with `fin ingest --kind orderbook`;
`fin reconcile <co> orderbook_vs_register` lists orders with no billing and billing with no
order."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.reconcile import visibility


@app.command("visibility")
def visibility_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Per backlog source: the items, what is still to come and the part falling in the 12 months
    after the last period end, against that period's core revenue. Straight-line over each item's
    remaining term; the sources are not added together."""

    def body(run: Run) -> None:
        data = visibility.show(run, company)
        typer.echo(
            json.dumps(data, indent=2, default=str)
            if json_output
            else visibility.format_visibility(data)
        )

    execute("visibility", {"company": company}, body)
