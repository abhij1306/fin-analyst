"""`fin payroll confirm-unit <company> <file> --annual|--monthly`: record the user's statement of a
payroll snapshot's unit (`fin.diligence.payroll`). The gross salary and CTC columns are read as
annual rupees; fin cannot tell, so the user confirms, and gate G1 waits for it."""

from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import payroll

payroll_app = typer.Typer(no_args_is_help=True, help="Payroll snapshot: confirm its unit.")
app.add_typer(payroll_app, name="payroll")


@payroll_app.command("confirm-unit")
def confirm_unit(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    file: Annotated[str, typer.Argument(help="The payroll file's name or file_id.")],
    annual: Annotated[
        bool, typer.Option("--annual", help="The user says amounts are annual.")
    ] = False,
    monthly: Annotated[
        bool, typer.Option("--monthly", help="The user says amounts are monthly.")
    ] = False,
) -> None:
    """Record the unit the user says a loaded payroll file's gross salary and CTC are in. Run it
    only when the user says which; fin warns at load when the amounts look monthly."""
    if annual == monthly:
        raise typer.BadParameter("give exactly one of --annual and --monthly (the user says which)")
    unit = "annual" if annual else "monthly"

    def body(run: Run) -> None:
        for line in payroll.confirm_unit(run, company, file, unit):
            typer.echo(line)

    execute("payroll confirm-unit", {"company": company, "file": file, "unit": unit}, body)
