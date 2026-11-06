"""`fin` command-line entry point."""

import json
from typing import Annotated

import typer

from fin import __version__
from fin.audit.errors import EXIT_FAILURES, EXIT_OK, FinError
from fin.doctor import run_checks
from fin.paths import data_dir, ensure_data_folder

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fin {__version__}")
        raise typer.Exit()


def _fail(exc: FinError) -> typer.Exit:
    typer.echo(str(exc), err=True)
    return typer.Exit(exc.exit_code)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local financial health analysis."""


@app.command()
def init() -> None:
    """Create the data folder tree. Safe to run again; never deletes anything."""
    root = data_dir()
    try:
        created, existing = ensure_data_folder(root)
    except FinError as exc:
        raise _fail(exc) from exc
    for folder in created:
        typer.echo(f"created  {folder}")
    typer.echo(f"data folder {root}: {len(created)} created, {len(existing)} already present")


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Check dependencies, versions and the data folder."""
    checks = run_checks(data_dir())
    failed = [check for check in checks if not check.ok]
    if json_output:
        payload = {"ok": not failed, "checks": [check.as_dict() for check in checks]}
        typer.echo(json.dumps(payload, indent=2))
    else:
        for check in checks:
            if check.ok:
                typer.echo(f"ok    {check.name}: {check.detail}")
            else:
                typer.echo(f"FAIL  {check.name}: {check.detail} [{check.code}] fix: {check.fix}")
        typer.echo("all checks passed" if not failed else f"{len(failed)} check(s) failed")
    raise typer.Exit(EXIT_FAILURES if failed else EXIT_OK)
