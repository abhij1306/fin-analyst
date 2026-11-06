"""`fin` command-line entry point. Every command runs through the audit logger
(`fin.audit.logger.execute`)."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin import __version__
from fin.audit.errors import EXIT_FAILURES, FileUnreadable, ReadOnlyViolation
from fin.audit.logger import Run, execute
from fin.config import load_config, write_default_config
from fin.db.migrate import MigrationResult, ensure_current, migrate_all
from fin.doctor import run_checks
from fin.ingest import COMPANY_NAME, files_in, ingest_file
from fin.map.commands import map_app, review_app
from fin.map.dictionary import load_label_map, load_seed
from fin.map.review import write_pending
from fin.paths import company_db_path, ensure_data_folder, inbox_dir
from fin.query.evidence import write_evidence
from fin.query.run import query
from fin.query.write import write_sql

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)
app.add_typer(map_app, name="map")
app.add_typer(review_app, name="review")


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


def _company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Load files already ingested again, as a new version."),
]


def _ingest_files(run: Run, jobs: list[tuple[str, list[Path]]], force: bool) -> None:
    """Ingest each company's files, printing progress as `[i/N] company/file: outcome`."""
    config = load_config(run.root)
    total = sum(len(files) for _, files in jobs)
    done = 0
    for company, files in jobs:
        result = ensure_current(run.root, company_db_path(run.root, company), "company", run)
        if result is not None:
            _report_migrations(run, [result])
        label_map = load_label_map(run.root, company)
        for path in files:
            done += 1
            outcome = ingest_file(run, config, company, path, label_map, force=force)
            typer.echo(f"[{done}/{total}] {company}/{path.name}: {outcome}")
        write_pending(run, company)
    if total == 0:
        typer.echo("no files to ingest")


@app.command()
def ingest(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    path: Annotated[
        Path | None,
        typer.Argument(help="A file or folder. Default: inbox/<company>/ in the data folder."),
    ] = None,
    force: ForceOption = False,
) -> None:
    """Load Excel and PDF documents for one company. Files already loaded are skipped (E105)."""

    def body(run: Run) -> None:
        source = path if path is not None else inbox_dir(run.root, company)
        _ingest_files(run, [(company, files_in(source))], force)

    execute("ingest", {"company": company, "path": path, "force": force}, body)


@app.command("ingest-batch")
def ingest_batch(
    folder: Annotated[
        Path, typer.Argument(help="A folder with one sub-folder per company, e.g. inbox/.")
    ],
    force: ForceOption = False,
) -> None:
    """Load every company sub-folder of FOLDER. Safe to re-run after an interruption: files
    already loaded are skipped (E105), so it resumes where it stopped."""

    def body(run: Run) -> None:
        if not folder.is_dir():
            raise FileUnreadable(f"{folder} is not a folder")
        jobs: list[tuple[str, list[Path]]] = []
        for entry in sorted(folder.iterdir()):
            if entry.is_dir() and COMPANY_NAME.match(entry.name):
                jobs.append((entry.name, files_in(entry)))
            elif not entry.name.startswith("."):
                run.skipped += 1
                message = (
                    f"{entry.name}: skipped; put files in a company folder named with lower-case "
                    "letters, digits, '-' and '_'"
                )
                run.event("ingest", message, level="warning", file=entry.name)
                typer.echo(message)
        _ingest_files(run, jobs, force)

    execute("ingest-batch", {"folder": folder, "force": force}, body)


@app.command("query")
def query_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    question: Annotated[str, typer.Option("--question", help="The question in words.")],
    sql: Annotated[str, typer.Option("--sql", help="One SELECT statement.")],
) -> None:
    """Answer a question with one read-only SELECT. Prints JSON rows with provenance columns
    (source_file, source_ref, source_confirmed) when the result has fact_id or raw_id. Anything
    other than a single SELECT is rejected (E502)."""

    def body(run: Run) -> None:
        typer.echo(json.dumps(query(run, company, question, sql), indent=2))

    execute("query", {"company": company, "question": question, "sql": sql}, body)


@app.command("evidence")
def evidence_command(
    query_id: Annotated[str, typer.Argument(help="The query_id printed by `fin query`.")],
) -> None:
    """Write an HTML page under evidence/ showing the source of every fact in a query's result.
    A missing source file is flagged on the page (E604, exit 1)."""

    def body(run: Run) -> None:
        path = write_evidence(run, query_id)
        typer.echo(json.dumps({"query_id": query_id, "evidence_path": str(path)}, indent=2))
        if run.failed:
            run.exit_code = EXIT_FAILURES

    execute("evidence", {"query_id": query_id}, body)


@app.command("sql")
def sql_command(
    company: Annotated[str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.")],
    sql: Annotated[str, typer.Option("--sql", help="The statement(s) to run.")],
    write: Annotated[
        bool, typer.Option("--write", help="Required: change data (backup + audit log).")
    ] = False,
) -> None:
    """Apply a data change the user asked for: back up the company database, run the SQL in one
    transaction, and record the statement, rows affected and backup in the audit log."""

    def body(run: Run) -> None:
        if not write:
            raise ReadOnlyViolation(
                "`fin sql` changes data only with --write; to read data use `fin query`"
            )
        typer.echo(json.dumps(write_sql(run, company, sql), indent=2))

    execute("sql", {"company": company, "sql": sql, "write": write}, body)
