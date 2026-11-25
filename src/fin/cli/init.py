"""`fin init` and `fin doctor`."""

import json
from typing import Annotated

import typer

from fin.audit.errors import EXIT_FAILURES
from fin.audit.logger import Run, execute
from fin.cli.core import app
from fin.config import write_default_config
from fin.db.schema import ensure_database
from fin.doctor import Check, run_checks
from fin.map.dictionary import load_seed
from fin.paths import analytics_db_path, audit_db_path, dictionary_db_path, ensure_data_folder


def _create_shared_databases(run: Run) -> None:
    for kind, path in (
        ("audit", audit_db_path(run.root)),
        ("dictionary", dictionary_db_path(run.root)),
        ("analytics", analytics_db_path(run.root)),
    ):
        created = ensure_database(path, kind)
        rel = path.relative_to(run.root).as_posix()
        line = f"{'created ' if created else 'kept    '} {rel}"
        typer.echo(line)
        run.event("database", line, file=rel, context={"created": created})


@app.command()
def init() -> None:
    """Create the data folder, config.toml and databases. Safe to run again; never deletes or
    overwrites. The PDF add-on is chosen with `fin parser use`."""

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
        _create_shared_databases(run)
        seed = load_seed(run.root, run)
        line = (
            f"dictionary: {seed.accounts_added} accounts and {seed.labels_added} label mappings "
            "added from templates/dictionary"
        )
        typer.echo(line)
        run.event("dictionary", line)
        typer.echo(
            f"data folder {run.root}: {len(created)} created, {len(existing)} already present"
        )

    execute("init", {}, body, audit_required=False)


def _check_level(check: Check) -> str:
    """Log level for a doctor check: error when it failed, warning when it warns, else info."""
    if not check.ok:
        return "error"
    if check.warning:
        return "warning"
    return "info"


def _check_line(check: Check) -> str:
    """One doctor check as a line of plain text."""
    if check.warning:
        return f"warn  {check.name}: {check.detail}; fix: {check.fix}"
    if check.ok:
        return f"ok    {check.name}: {check.detail}"
    return f"FAIL  {check.name}: {check.detail} [{check.code}] fix: {check.fix}"


def _print_checks(checks: list[Check], failed: int, json_output: bool) -> None:
    """Print the doctor results as JSON or as plain text."""
    if json_output:
        payload = {"ok": not failed, "checks": [check.as_dict() for check in checks]}
        typer.echo(json.dumps(payload, indent=2))
        return
    for check in checks:
        typer.echo(_check_line(check))
    typer.echo("all checks passed" if not failed else f"{failed} check(s) failed")


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
                level=_check_level(check),
                code=check.code,
                context=None if check.fix is None else {"fix": check.fix},
            )
        _print_checks(checks, len(failed), json_output)
        if failed:
            run.failed = len(failed)
            run.exit_code = EXIT_FAILURES

    execute("doctor", {"json": json_output}, body, audit_required=False)
