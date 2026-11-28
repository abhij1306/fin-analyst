"""`fin findings` and `fin status`."""

import json
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company, check_optional_company
from fin.findings import format_finding, list_findings
from fin.paths import data_dir
from fin.services import decisions
from fin.services.result import show
from fin.status import companies, format_status, status

findings_app = typer.Typer(
    no_args_is_help=True,
    help="Findings: figures in what a company filed that do not add up. Show them; save as is.",
)
app.add_typer(findings_app, name="findings")


@findings_app.command("list")
def findings_list(
    company: Annotated[
        str | None, typer.Argument(callback=check_optional_company, help="Only this company.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Open findings, then accepted ones: this document's figure and what it was compared with,
    each with its file and page or cell. A document with open findings is held: not in current
    figures and metrics."""

    def body(run: Run) -> None:
        names = [company] if company is not None else companies(run.root)
        rows = [row for name in names for row in list_findings(run, name)]
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(format_finding, rows)) if rows else "no findings")

    execute("findings list", {"company": company, "json": json_output}, body)


@findings_app.command("accept")
def findings_accept(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    ids: Annotated[list[int], typer.Argument(help="Finding ids from `fin findings list`.")],
) -> None:
    """Save as is: accept open findings. A held document with no open findings left is loaded
    exactly as filed; its findings stay attached, marked accepted."""

    show(decisions.accept_findings(data_dir(), company, ids, via="cli", actor=from_environment()))


@app.command("status")
def status_command(
    company: Annotated[
        str | None, typer.Argument(callback=check_optional_company, help="Only this company.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Per company: periods loaded and gaps, held documents, open and accepted findings, mappings
    waiting for review, figures read by Claude or the PDF add-on, inbox files not yet ingested,
    and an open diligence engagement's queries, proposed adjustments and gates."""

    def body(run: Run) -> None:
        result = status(run, company)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(format_status(result))

    execute("status", {"company": company, "json": json_output}, body)
