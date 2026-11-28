"""`fin dataroom scan|plan` (SPEC-13): scan a data room into a plan and show it. Nothing loads."""

from pathlib import Path
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.config import load_config
from fin.dataroom.scan import format_plan, plan_json, read_plan, scan

dataroom_app = typer.Typer(
    no_args_is_help=True, help="Scan a data room into a plan of what each file is (loads nothing)."
)
app.add_typer(dataroom_app, name="dataroom")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Print the plan as JSON.")]


def _show(run: Run, company: str, json_output: bool) -> None:
    rows = read_plan(run.root, company)
    typer.echo(plan_json(rows) if json_output else format_plan(company, rows))


@dataroom_app.command("scan")
def scan_command(
    company: Company,
    folder: Annotated[
        Path, typer.Argument(help="The data room: a folder, searched with its sub-folders.")
    ],
    json_output: JsonOption = False,
) -> None:
    """Walk FOLDER: unpack .zip archives (into dataroom/<company>/ in the data folder), list
    duplicate files once, name each file's kind, and list what fin cannot use with the reason and a
    next step. Re-running adds only new files. Loads nothing."""

    def body(run: Run) -> None:
        found = scan(run, load_config(run.root), company, folder)
        if not json_output:
            typer.echo(
                f"scanned {found.folder}: {found.new} new in the plan, "
                f"{found.duplicates} new duplicate paths"
            )
        _show(run, company, json_output)

    execute("dataroom scan", {"company": company, "folder": folder, "json": json_output}, body)


@dataroom_app.command("plan")
def plan_command(company: Company, json_output: JsonOption = False) -> None:
    """Show the company's data-room plan: each file's kind, profile and period, its duplicates,
    and what will be skipped."""
    execute(
        "dataroom plan",
        {"company": company, "json": json_output},
        lambda run: _show(run, company, json_output),
    )
