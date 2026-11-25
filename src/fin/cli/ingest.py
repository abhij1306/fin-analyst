"""`fin ingest`, `fin ingest-batch` and `fin verify`."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import FileUnreadable
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company, echo_refresh
from fin.config import load_config
from fin.db.schema import ensure_database
from fin.diligence.bank import ingest_bank
from fin.diligence.sources import ingest_source
from fin.extract.profiles import KINDS
from fin.ingest import files_in, ingest_file
from fin.map.dictionary import load_label_map
from fin.map.review import write_pending
from fin.metrics.export import refresh_analytics
from fin.paths import COMPANY_NAME, company_db_path, inbox_dir
from fin.verify import format_result, verify

ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Load files already ingested again, as a new version."),
]


def _ingest_files(
    run: Run, jobs: list[tuple[str, list[Path]]], force: bool, reading: Path | None = None
) -> None:
    """Ingest each company's files, printing progress as `[i/N] company/file: outcome`."""
    config = load_config(run.root)
    total = sum(len(files) for _, files in jobs)
    done = 0
    for company, files in jobs:
        ensure_database(company_db_path(run.root, company), "company")
        label_map = load_label_map(run.root, company)
        for path in files:
            done += 1
            outcome = ingest_file(
                run, config, company, path, label_map, force=force, reading=reading
            )
            typer.echo(f"[{done}/{total}] {company}/{path.name}: {outcome}")
        write_pending(run, company)
    if total == 0:
        typer.echo("no files to ingest")
    echo_refresh(run)


@app.command()
def ingest(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    path: Annotated[
        Path | None,
        typer.Argument(help="A file or folder. Default: inbox/<company>/ in the data folder."),
    ] = None,
    force: ForceOption = False,
    reading: Annotated[
        Path | None,
        typer.Option(
            "--reading",
            help="A reading file (CSV of figures Claude read from PATH's pages or cells): loaded "
            "for the statements fin cannot read. PATH must be one file.",
        ),
    ] = None,
    kind: Annotated[
        str | None,
        typer.Option(
            "--kind",
            help=f"Load PATH as a diligence source file: {' or '.join(KINDS)} (needs an open "
            "engagement; the period comes from the export's header).",
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="With --kind: the column profile (templates/profiles/<name>.toml). Default: the "
            "first profile of the kind whose headers the file has.",
        ),
    ] = None,
) -> None:
    """Load Excel and PDF documents for one company. Files already loaded are skipped (E105).
    With --kind, load one diligence source file (trial balance, Ledger Vouchers, registers, bank
    statement, payroll)
    instead."""

    # Usage errors, before the run starts (like the option callbacks).
    if reading is not None and (path is None or not path.is_file()):
        raise typer.BadParameter("--reading needs PATH to be the one file it was read from")
    if kind is not None and (kind not in KINDS or path is None or not path.is_file()):
        raise typer.BadParameter(f"--kind takes {' or '.join(KINDS)} and PATH as one file")
    if kind is not None and (force or reading is not None):
        raise typer.BadParameter("--kind does not take --force or --reading")
    if profile is not None and kind is None:
        raise typer.BadParameter("--profile needs --kind")

    def body(run: Run) -> None:
        if kind is not None and path is not None:
            ensure_database(company_db_path(run.root, company), "company")
            config = load_config(run.root)
            if kind == "bank":
                loaded = ingest_bank(run, config, company, path, profile)
            else:
                loaded = ingest_source(run, config, company, path, kind, profile)
            typer.echo(f"{company}/{path.name}: " + "\n  ".join(loaded.lines))
            return
        source = path if path is not None else inbox_dir(run.root, company)
        _ingest_files(run, [(company, files_in(source))], force, reading)

    args = {"company": company, "path": path, "force": force, "reading": reading}
    execute("ingest", args | {"kind": kind, "profile": profile}, body)


@app.command("ingest-batch")
def ingest_batch(
    folder: Annotated[
        Path | None,
        typer.Argument(help="A folder with one sub-folder per company. Default: inbox/."),
    ] = None,
    force: ForceOption = False,
) -> None:
    """Load every company sub-folder of FOLDER. Safe to re-run after an interruption: files
    already loaded are skipped (E105), so it resumes where it stopped."""

    def body(run: Run) -> None:
        source = folder if folder is not None else run.root / "inbox"
        if not source.is_dir():
            raise FileUnreadable(f"{source} is not a folder")
        jobs: list[tuple[str, list[Path]]] = []
        for entry in sorted(source.iterdir()):
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


@app.command("verify")
def verify_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    path: Annotated[Path, typer.Argument(help="The original document the reading was read from.")],
    reading: Annotated[
        Path, typer.Option("--reading", help="An independent reading of the document (CSV).")
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Compare a Claude reading of a document with what is loaded for it: agree, differ,
    missing in fin, missing in reading. Run it on any loaded or held document, any number of times;
    each run and its lines are recorded (verify_runs, verify_lines). Lines that do not agree are
    findings (shown, and recorded in the audit log), not errors. No figure is changed."""

    def body(run: Run) -> None:
        result = verify(run, company, path, reading)
        if json_output:
            refresh_analytics(run)
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(format_result(result))
            echo_refresh(run)

    execute(
        "verify",
        {"company": company, "path": path, "reading": reading, "json": json_output},
        body,
    )
