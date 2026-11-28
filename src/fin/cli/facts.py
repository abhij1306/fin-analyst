"""`fin fact state|unstate|list`: figures the user states (fin.facts)."""

import json
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.facts import (
    correct,
    format_corrected,
    format_stated,
    list_corrected,
    list_stated,
    state,
    unstate,
)
from fin.metrics.export import Refresh


def echo_refresh(refresh: Refresh) -> None:
    """The analytics refresh the fin.facts command already ran (as fin.cli.core.echo_refresh)."""
    typer.echo(
        f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
        f"{refresh.metric_rows} metric rows"
    )


fact_app = typer.Typer(
    no_args_is_help=True,
    help="Figures the user states: a nil for a line the documents do not have, or a value from a "
    "source fin cannot hold. Stored as the user's (read_by user), never as a document's. And "
    "corrections of a figure fin read wrongly.",
)
app.add_typer(fact_app, name="fact")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]


@fact_app.command("state")
def fact_state(
    company: Company,
    account: Annotated[str, typer.Option("--account", help="Account, e.g. finance_cost.")],
    period: Annotated[str, typer.Option("--period", help="FY24, FY26-Q1, FY26-H1 or FY26-9M.")],
    scope: Annotated[str, typer.Option("--scope", help="standalone or consolidated.")],
    value: Annotated[
        str,
        typer.Option(
            "--value", help="In rupees (rupees per share for EPS); 0 for nil. Negative: --value=-5."
        ),
    ],
    reason: Annotated[str, typer.Option("--reason", help="Why the figure is stated (recorded).")],
) -> None:
    """State a figure: back up the company database, store it with the reason, and log it. A
    figure a loaded document already gives is not stated: a finding records both values."""

    actor = from_environment()

    def body(run: Run) -> None:
        result = state(run, company, account, period, scope, value, reason, actor=actor)
        if result["outcome"] == "stated":
            typer.echo(
                f"stated #{result['stated_id']}: {account} {period} {scope} = {result['value']} "
                f"(backup {result['backup_path']})"
            )
        else:
            doc = result["document"]
            typer.echo(
                f"not stated: {doc['file_name']} {doc['source']} gives {account} {period} {scope}; "
                f"finding #{result['finding_id']} (`fin findings list {company}`)"
            )
        echo_refresh(result["refresh"])

    args = {"company": company, "account": account, "period": period, "scope": scope}
    execute("fact state", args | {"value": value, "reason": reason, "actor": actor}, body)


@fact_app.command("unstate")
def fact_unstate(
    company: Company,
    stated_id: Annotated[int, typer.Argument(help="Stated figure id from `fin fact list`.")],
) -> None:
    """Withdraw a stated figure (backed up and logged; the row stays, marked withdrawn)."""

    actor = from_environment()

    def body(run: Run) -> None:
        result = unstate(run, company, stated_id, actor=actor)
        typer.echo(
            f"withdrawn #{stated_id}: {result['account']} {result['period']} {result['scope']}"
        )
        echo_refresh(result["refresh"])

    execute("fact unstate", {"company": company, "stated_id": stated_id, "actor": actor}, body)


@fact_app.command("correct")
def fact_correct(
    company: Company,
    fact_id: Annotated[int, typer.Argument(help="The fact fin misread (fin audit, fin sql).")],
    value: Annotated[
        str,
        typer.Option(
            "--value",
            help="As printed, in rupees (rupees per share for EPS). Negative: --value=-5.",
        ),
    ],
    reason: Annotated[str, typer.Option("--reason", help="What fin misread (recorded).")],
) -> None:
    """Correct a figure fin read wrongly, only on the user's word: back up, set the value, keep the
    cell and the printed text, record the value before, the reason and who ran it, and check held
    documents again. For fin's own misread only, never to make figures tie: in a diligence
    engagement a figure under a query or an adjustment is refused (E409); raise a query instead."""

    actor = from_environment()

    def body(run: Run) -> None:
        result = correct(run, company, fact_id, value, reason, actor=actor)
        typer.echo(
            f"corrected #{result['correction_id']}: fact {fact_id} {result['account']} "
            f"{result['period']} {result['scope']} {result['original_value']} -> "
            f"{result['value']} ({result['source']}, printed {result['printed']}; backup "
            f"{result['backup_path']})"
        )
        for doc in result["held_documents_rechecked"]:
            typer.echo(
                f"  rechecked doc_id {doc['doc_id']} {doc['file']}: {doc['open']} open findings, "
                f"{doc['status']}"
            )
        echo_refresh(result["refresh"])

    args = {"company": company, "fact_id": fact_id, "value": value, "reason": reason}
    execute("fact correct", args | {"actor": actor}, body)


@fact_app.command("list")
def fact_list(
    company: Company,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """The stated figures not withdrawn, with their reasons and whether they are in use, and the
    corrections in force."""

    def body(run: Run) -> None:
        rows = list_stated(run.root, company)
        corrected = list_corrected(run.root, company)
        if json_output:
            payload = {"stated": rows, "corrected": corrected}
            typer.echo(json.dumps(payload, indent=2, default=str))
            return
        typer.echo("\n".join(map(format_stated, rows)) if rows else "no stated figures")
        typer.echo(
            "\n".join(map(format_corrected, corrected)) if corrected else "no corrected figures"
        )

    execute("fact list", {"company": company, "json": json_output}, body)
