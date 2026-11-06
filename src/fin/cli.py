"""`fin` command-line entry point. Every command runs through the audit logger
(`fin.audit.logger.execute`)."""

import json
from typing import Annotated

import typer

from fin import __version__
from fin.audit.errors import EXIT_FAILURES
from fin.audit.logger import Run, execute
from fin.config import write_default_config
from fin.db.migrate import MigrationResult, migrate_all
from fin.doctor import run_checks
from fin.paths import ensure_data_folder

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fin {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local financial health analysis."""


def _report_migrations(run: Run, results: list[MigrationResult]) -> None:
    for result in results:
        if result.from_version == result.to_version:
            line = f"{result.db_file}: up to date at schema version {result.to_version}"
        else:
            backup = result.backup or "none, new file"
            line = (
                f"{result.db_file}: schema version {result.from_version} -> {result.to_version} "
                f"(backup: {backup})"
            )
        typer.echo(line)
        run.event(
            "migrate",
            line,
            file=result.db_file,
            context={
                "from_version": result.from_version,
                "to_version": result.to_version,
                "backup": result.backup,
            },
        )


@app.command()
def init() -> None:
    """Create the data folder, config.toml and databases. Safe to run again; never deletes or
    overwrites."""

    def body(run: Run) -> None:
        created, existing = ensure_data_folder(run.root)
        for folder in created:
            typer.echo(f"created  {folder}")
        run.event(
            "folders",
            f"{len(created)} created, {len(existing)} already present",
            context={"created": [str(folder) for folder in created]},
        )
        if write_default_config(run.root):
            typer.echo("created  config.toml")
            run.event("config", "wrote default config.toml")
        else:
            typer.echo("kept     config.toml (already present, not changed)")
            run.event("config", "config.toml already present, not changed")
        _report_migrations(run, migrate_all(run.root, run))
        typer.echo(
            f"data folder {run.root}: {len(created)} created, {len(existing)} already present"
        )

    execute("init", {}, body, audit_required=False)


@app.command()
def migrate() -> None:
    """Apply pending database migrations, backing up each file first. Safe to run again."""

    def body(run: Run) -> None:
        _report_migrations(run, migrate_all(run.root, run))

    execute("migrate", {}, body, audit_required=False)


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Check dependencies, versions, the data folder, config.toml and the audit schema."""

    def body(run: Run) -> None:
        checks = run_checks(run.root)
        failed = [check for check in checks if not check.ok]
        for check in checks:
            run.event(
                "check",
                f"{check.name}: {check.detail}",
                level="info" if check.ok else "error",
                code=check.code,
                context=None if check.fix is None else {"fix": check.fix},
            )
        if json_output:
            payload = {"ok": not failed, "checks": [check.as_dict() for check in checks]}
            typer.echo(json.dumps(payload, indent=2))
        else:
            for check in checks:
                if check.ok:
                    typer.echo(f"ok    {check.name}: {check.detail}")
                else:
                    typer.echo(
                        f"FAIL  {check.name}: {check.detail} [{check.code}] fix: {check.fix}"
                    )
            typer.echo("all checks passed" if not failed else f"{len(failed)} check(s) failed")
        if failed:
            run.failed = len(failed)
            run.exit_code = EXIT_FAILURES

    execute("doctor", {"json": json_output}, body, audit_required=False)
