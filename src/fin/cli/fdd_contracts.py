"""`fin sample`, `fin walkthrough` and `fin contracts`: FDD contracts and deal walkthroughs
(docs/fdd.md F1).

`sample draw` draws a reproducible sample of register lines (`fin.sampling.draw`); `walkthrough
record` stores Claude's checks of each line and `walkthrough summary` totals them
(`fin.sampling.walkthrough`); an exception at or above the threshold is a line of the
`walkthrough` reconciliation for a query. `contracts coverage` shows whether the top customers and
vendors have a contract on file (`fin.reconcile.contract_coverage`). Contract readings load with
`fin recast contracts`. Nothing is fixed: differences are queried."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.reconcile import contract_coverage
from fin.reconcile.format import format_result
from fin.sampling import draw as draw_module
from fin.sampling import walkthrough

sample_app = typer.Typer(no_args_is_help=True, help="Samples of register lines for walkthroughs.")
walkthrough_app = typer.Typer(no_args_is_help=True, help="Claude's walkthroughs of sample lines.")
contracts_app = typer.Typer(no_args_is_help=True, help="Contract coverage of the top parties.")
app.add_typer(sample_app, name="sample")
app.add_typer(walkthrough_app, name="walkthrough")
app.add_typer(contracts_app, name="contracts")
CompanyArg = Annotated[
    str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _kind(value: str) -> str:
    if value not in draw_module.PARTY:
        raise typer.BadParameter("use sales or purchase")
    return value


@sample_app.command("draw")
def draw_command(
    company: CompanyArg,
    kind: Annotated[
        str, typer.Option("--kind", callback=_kind, help="sales or purchase register lines.")
    ],
    period: Annotated[str, typer.Option("--period", help="The period, e.g. FY25.")],
    n: Annotated[int, typer.Option("--n", help="Lines in the sample.")] = 25,
    seed: Annotated[
        int | None, typer.Option("--seed", help="Random seed (default: a new one, stored).")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Draw a sample: every line at or above the engagement threshold (largest first, up to n),
    then the rest at random. The seed is stored, so the draw can be repeated."""

    def body(run: Run) -> None:
        sample = draw_module.draw(run, company, kind, period, n, seed)
        typer.echo(
            json.dumps(sample, indent=2, default=str)
            if json_output
            else draw_module.format_sample(sample)
        )

    execute("sample draw", {"company": company, "kind": kind, "period": period, "n": n}, body)


@sample_app.command("show")
def show_command(
    company: CompanyArg,
    sample_id: Annotated[int | None, typer.Argument(help="Sample id (default: list them).")] = None,
    json_output: JsonOption = False,
) -> None:
    """List the samples, or show one sample's lines (the line numbers a walkthrough uses)."""

    def body(run: Run) -> None:
        samples = draw_module.list_samples(run, company, sample_id)
        if json_output:
            typer.echo(json.dumps(samples, indent=2, default=str))
        else:
            typer.echo("\n".join(map(draw_module.format_sample, samples)) or "no samples")

    execute("sample show", {"company": company, "sample_id": sample_id}, body)


@walkthrough_app.command("record")
def record_command(
    company: CompanyArg,
    sample_id: Annotated[int, typer.Argument(help="The sample's id (`fin sample show`).")],
    file: Annotated[Path, typer.Argument(help="JSON list of Claude's results (file + page).")],
    json_output: JsonOption = False,
) -> None:
    """Record Claude's walkthrough of a sample: one result per line and check (pass, exception or
    not_available) with evidence. An exception at or above the threshold becomes a line for a
    query (`fin query raise --items`). Whether Claude's reading is trusted is the user's call."""
    if not file.is_file():
        raise typer.BadParameter(f"{file} is not a file")

    def body(run: Run) -> None:
        result = walkthrough.record(run, company, sample_id, file)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(walkthrough.format_summary(result["summary"]))
            typer.echo(format_result(result["recon"]))

    execute("walkthrough record", {"company": company, "sample_id": sample_id, "file": file}, body)


@walkthrough_app.command("summary")
def summary_command(company: CompanyArg, json_output: JsonOption = False) -> None:
    """Per sample: lines, lines walked, checks passed, exceptions and their amount."""

    def body(run: Run) -> None:
        rows = walkthrough.list_summaries(run, company)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(walkthrough.format_summary, rows)) or "no samples")

    execute("walkthrough summary", {"company": company}, body)


@contracts_app.command("coverage")
def coverage_command(company: CompanyArg, json_output: JsonOption = False) -> None:
    """The top customers (core revenue) and vendors (purchases) of the last period, each with its
    contract on file or not, expiry within 12 months of the period end, auto-renew, change of
    control and lock-in left. How many: `[fdd] coverage_top_n` in config.toml."""

    def body(run: Run) -> None:
        rows = contract_coverage.show(run, company)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo(contract_coverage.format_coverage(rows))

    execute("contracts coverage", {"company": company}, body)
