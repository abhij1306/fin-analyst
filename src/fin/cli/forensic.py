"""`fin forensic run|status|flags|settings|set`: the forensic tests (SPEC-18, fin.forensic).

A flag is the company's to explain: Claude drafts a query for it (`fin query raise --flags`) and
the user approves it; nothing is changed to clear a flag. Every command calls
`fin.services.forensic`, as the Forensic and Settings screens do."""

import json
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app
from fin.diligence.cli_options import CompanyArg, JsonOption, period_option
from fin.diligence.engagement import rupees
from fin.paths import data_dir
from fin.services import forensic as services
from fin.services.result import show

forensic_app = typer.Typer(
    no_args_is_help=True,
    help="Forensic tests: flags for the company to explain, never scores.",
)
app.add_typer(forensic_app, name="forensic")

TestOption = Annotated[
    str | None, typer.Option("--test", help="Only this test, e.g. je_round (see status).")
]
PeriodOption = Annotated[
    str | None,
    typer.Option("--period", callback=period_option, help="Only this period, e.g. FY25."),
]


@forensic_app.command("run")
def forensic_run(company: CompanyArg, test: TestOption = None, period: PeriodOption = None) -> None:
    """Run the forensic tests for the open engagement's periods. Every run is kept; the latest is
    current."""
    show(services.run_tests(data_dir(), company, test, period, via="cli", actor=from_environment()))


@forensic_app.command("status")
def forensic_status(company: CompanyArg, json_output: JsonOption = False) -> None:
    """Each test per period: ran (flags, how many at or above the threshold), stale, too few
    entries (the count and the minimum), cannot run (what it needs) or not run."""

    def body(run: Run) -> None:
        rows = services.read_status(run, data_dir(), company)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
            return
        for row in rows:
            typer.echo(f"{row['test']:<16} {row['period']:<14} {row['text']}")

    execute("forensic status", {"company": company, "json": json_output}, body)


def flag_line(row: dict) -> str:  # type: ignore[type-arg]
    query = f"; query #{row['query_id']} ({row['query_status']})" if row.get("query_id") else ""
    stale = " [stale]" if row["stale"] else ""
    over = "" if row["over_threshold"] else " (below the threshold)"
    return (
        f"#{row['flag_id']} {row['test']} {row['period']}{stale}: {rupees(row['amount'])}{over} "
        f"{row['ledger'] or ''} - {row['reason']} [{row['source']}]{query}"
    )


@forensic_app.command("flags")
def forensic_flags(
    company: CompanyArg,
    test: TestOption = None,
    period: PeriodOption = None,
    over_threshold: Annotated[
        bool, typer.Option("--over-threshold", help="Only flags that can go to a query.")
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """The flags of the latest runs, largest amount first, each with its source and query."""

    def body(run: Run) -> None:
        rows = services.read_flags(
            run, data_dir(), company, test=test, period=period, over_threshold=over_threshold
        )
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(flag_line, rows)) if rows else "no flags")

    args = {"company": company, "test": test, "period": period, "over": over_threshold}
    execute("forensic flags", {**args, "json": json_output}, body)


@forensic_app.command("settings")
def forensic_settings(company: CompanyArg, json_output: JsonOption = False) -> None:
    """The forensic settings the company's tests use, with their defaults and reasons."""

    def body(run: Run) -> None:
        rows = services.read_settings(data_dir(), company)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
            return
        for row in rows:
            typer.echo(f"{row['key']} = {row['value']} ({row['from']}; default {row['default']})")
            typer.echo(f"    {row['reason']}")

    execute("forensic settings", {"company": company, "json": json_output}, body)


@forensic_app.command("set")
def forensic_set(
    company: CompanyArg,
    key: Annotated[str, typer.Argument(help="The setting, e.g. approval_limits.")],
    value: Annotated[
        str,
        typer.Argument(help='The value; a list comma-separated; "default" removes the company\'s.'),
    ],
) -> None:
    """Change one forensic setting for the company (config.toml [forensic.companies.<name>]).
    The runs it feeds become stale. Set one only when the user gives it."""
    show(services.set_setting(data_dir(), company, key, value, via="cli", actor=from_environment()))
