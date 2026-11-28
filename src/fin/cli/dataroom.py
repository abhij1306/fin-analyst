"""`fin dataroom scan|plan|propose|approve|load` (SPEC-13): scan a data room into a plan, decide its
rows and load the approved ones in one run."""

from pathlib import Path
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.dataroom.load import PROPOSABLE, propose
from fin.dataroom.scan import format_plan, plan_json, read_plan
from fin.paths import data_dir
from fin.services import dataroom
from fin.services.result import Refused, show

dataroom_app = typer.Typer(
    no_args_is_help=True,
    help="Scan a data room into a plan, approve its rows and load them in one run.",
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

    result = dataroom.scan_folder(data_dir(), company, folder, via="cli", actor=from_environment())
    if isinstance(result, Refused) or not json_output:
        show(result)
    rows = dataroom.plan_rows(data_dir(), company)
    typer.echo(plan_json(rows) if json_output else format_plan(company, rows))


@dataroom_app.command("plan")
def plan_command(company: Company, json_output: JsonOption = False) -> None:
    """Show the company's data-room plan: each file's kind, profile and period, its duplicates,
    and what will be skipped."""
    execute(
        "dataroom plan",
        {"company": company, "json": json_output},
        lambda run: _show(run, company, json_output),
    )


@dataroom_app.command("propose")
def propose_command(
    company: Company,
    file_id: Annotated[int, typer.Argument(help="The plan row (its [id] in fin dataroom plan).")],
    kind: Annotated[str, typer.Option("--kind", help=f"One of: {', '.join(PROPOSABLE)}.")],
    reason: Annotated[str, typer.Option("--reason", help="Why the file is of this kind.")],
    profile: Annotated[
        str | None, typer.Option("--profile", help="The column profile (needed for gst).")
    ] = None,
) -> None:
    """The assistant's kind for a file fin could not name. The user confirms it by approving the
    row (fin dataroom approve)."""

    def body(run: Run) -> None:
        row = propose(run, company, file_id, kind, profile, reason)
        typer.echo(f"[{row.file_id}] {row.path}: proposed as {kind}; waits for the user's approval")

    args = {"company": company, "file_id": file_id, "kind": kind, "profile": profile}
    execute("dataroom propose", args | {"reason": reason}, body)


@dataroom_app.command("approve")
def approve_command(
    company: Company,
    ids: Annotated[list[int], typer.Argument(help="Plan rows to load (their [id]s).")],
    undo: Annotated[bool, typer.Option("--undo", help="Take the approval back.")] = False,
) -> None:
    """The user's choice of rows to load. Approving a row with a proposed kind confirms that kind.
    Run it only when the user says which rows."""

    show(
        dataroom.approve_rows(
            data_dir(), company, ids, via="cli", actor=from_environment(), undo=undo
        )
    )


@dataroom_app.command("load")
def load_command(company: Company) -> None:
    """Load every approved row not loaded yet, under one run id, each document in its own
    transaction, with one outcome line per file. A file already loaded is not loaded twice."""

    show(dataroom.load_plan(data_dir(), company, via="cli", actor=from_environment()))
