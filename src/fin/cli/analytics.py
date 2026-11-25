"""`fin analytics <company>`: customer concentration, retention, cohorts, sector mix and
price/volume/mix, from the registers (`fin.diligence.analytics`)."""

import json
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import analytics


@app.command("analytics")
def analytics_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print the views as JSON.")] = False,
) -> None:
    """Customer, product, sector, pricing and retention analytics of the latest engagement's sales
    register (views customer_concentration, customer_retention, customer_cohorts, sector_mix,
    product_pvm). Core and pass-through revenue are separate; customers are tokens."""

    def body(run: Run) -> None:
        data = analytics.collect(run, company)
        if json_output:
            typer.echo(json.dumps(data, indent=2, default=str))
        else:
            typer.echo(analytics.format_report(company, data))

    execute("analytics", {"company": company, "json": json_output}, body)
