"""`fin query list|raise|edit|approve|sent|answer|close|withdraw` and `fin gate status|approve`
(`fin adjust` is in `adjust_cli.py`, `fin engagement` in `engagement_cli.py`). The logic is in
`queries.py` and `gates.py`. fin never sends a query: `fin query sent` records that the user
sent it."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.config import SCOPES
from fin.diligence import (
    engagement,
    export_cli,
    gates,
    inquiries,
    inquiry_cli,
    queries,
)
from fin.diligence.cli_checks import check_raise_options
from fin.diligence.cli_options import (
    CompanyArg,
    JsonOption,
    QueryIds,
    choice_of,
    echo_rows,
    period_option,
    rupees_option,
    text_option,
)
from fin.forensic.handoff import raise_from_flags
from fin.paths import data_dir
from fin.reconcile import items as recon_items
from fin.services import decisions
from fin.services import diligence as services
from fin.services.result import show

query_app = typer.Typer(
    no_args_is_help=True,
    help="Accountant queries: Claude drafts, the user approves and sends; fin never sends.",
)


# fin query


@query_app.command("list")
def query_list(
    company: CompanyArg,
    status: Annotated[
        str | None,
        typer.Option(callback=choice_of(engagement.QUERY_STATUSES), help="Only this status."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """The open engagement's queries, with amounts, sources and answers."""

    def body(run: Run) -> None:
        if not engagement.is_open(run, company):
            typer.echo("[]" if json_output else f"{company} has no open engagement: no queries")
            return
        rows = queries.list_queries(run, company, status)
        echo_rows(rows, json_output, queries.format_query, "no queries")

    execute("query list", {"company": company, "status": status, "json": json_output}, body)


@query_app.command("raise")
def query_raise(
    company: CompanyArg,
    question: Annotated[
        str,
        typer.Option("--question", callback=text_option, help="The question for the accountant."),
    ],
    finding: Annotated[
        int | None,
        typer.Option("--finding", help="Raise it from this finding (`fin findings list`)."),
    ] = None,
    amount: Annotated[
        Decimal | None,
        typer.Option(
            "--amount",
            parser=rupees_option,
            metavar="RUPEES",
            help="Without --finding: the difference.",
        ),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period", callback=period_option, help="Without --finding: the period, e.g. FY25."
        ),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope",
            callback=choice_of(SCOPES),
            help="Without --finding: standalone or consolidated.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            callback=text_option,
            help="Without --finding: where the figures are (file + sheet!cell or page).",
        ),
    ] = None,
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            callback=text_option,
            help="An inquiry for management on this topic (config.toml [fdd] inquiry_topics): "
            "no amount or source; --period and --scope are optional.",
        ),
    ] = None,
    items: Annotated[
        str | None,
        typer.Option(
            "--items",
            metavar="IDS",
            help="One issue for these reconciliation lines (item ids from `fin reconcile`, "
            "e.g. 12,13,14; one period, at or above the threshold).",
        ),
    ] = None,
    flags: Annotated[
        str | None,
        typer.Option(
            "--flags",
            metavar="IDS",
            help="One query for these forensic flags (ids from `fin forensic flags`; one "
            "period, at or above the threshold).",
        ),
    ] = None,
) -> None:
    """Raise a draft query: from a finding (its period, scope, difference and sources; the finding
    is not changed), from reconciliation lines (--items: one issue, many lines), as an inquiry
    for management (--topic), from forensic flags (--flags), or with --amount, --period and
    --source."""

    # Usage errors, before the run starts (like the option callbacks).
    item_ids = check_raise_options(items, finding, (amount, period, scope, source), topic, flags)

    def body(run: Run) -> None:
        if topic is not None:
            row = inquiries.raise_inquiry(
                run, company, topic=topic, question=question, period=period, scope=scope
            )
            typer.echo(queries.format_query(row))
            return
        if flags is not None:
            row = raise_from_flags(run, data_dir(), company, item_ids, question)
            typer.echo(queries.format_query(row))
            typer.echo(f"    flags: {len(item_ids)}")
            return
        if items is not None:
            row, summary = recon_items.raise_from_items(run, company, item_ids, question)
            typer.echo(queries.format_query(row))
            typer.echo(f"    lines: {len(item_ids)} ({summary})")
            return
        if finding is not None:
            row = queries.raise_from_finding(run, company, finding, question)
        else:
            assert amount is not None and period is not None and source is not None
            row = queries.raise_manual(
                run,
                company,
                amount=amount,
                period=period,
                scope=scope,
                sources=source,
                question=question,
            )
        typer.echo(queries.format_query(row))

    args = {
        "company": company,
        "question": question,
        "finding": finding,
        "amount": amount,
        "period": period,
        "scope": scope,
        "source": source,
        "items": items,
        "flags": flags,
        "topic": topic,
    }
    execute("query raise", args, body)


@query_app.command("edit")
def query_edit(
    company: CompanyArg,
    query_id: Annotated[int, typer.Argument(help="A draft query's id.")],
    question: Annotated[
        str, typer.Option("--question", callback=text_option, help="The new wording.")
    ],
) -> None:
    """Change the wording of a draft query."""

    show(
        services.edit_query(
            data_dir(), company, query_id, question, via="cli", actor=from_environment()
        )
    )


def _status_command(name: str, target: str, doc: str) -> None:
    """`fin query <name> <co> <ids...>`: move queries to `target`."""

    def command(company: CompanyArg, ids: QueryIds) -> None:
        show(
            decisions.set_query_status(
                data_dir(), company, ids, target, via="cli", actor=from_environment()
            )
        )

    command.__doc__ = doc
    query_app.command(name)(command)


_status_command("approve", "approved", "Approve draft queries (when the user approves them).")
_status_command(
    "sent", "sent", "Mark approved queries sent, when the user says they sent them. fin sends none."
)


@query_app.command("answer")
def query_answer(
    company: CompanyArg,
    query_id: Annotated[int, typer.Argument(help="A sent query's id.")],
    text: Annotated[
        str | None, typer.Option("--text", callback=text_option, help="The accountant's answer.")
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", callback=text_option, help="Where the answer is (a file, an email)."
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="The reply file: archived like a source. A .txt, .eml or .md reply is read "
            "as written and is the answer; any other file needs --text.",
        ),
    ] = None,
) -> None:
    """Record the accountant's answer to a sent query, as text and/or a reply file."""
    if text is None and file is None:
        raise typer.BadParameter("give --text, or --file with the reply")
    answer = services.Answer(text, source, file)
    show(
        services.answer_query(
            data_dir(), company, query_id, answer, via="cli", actor=from_environment()
        )
    )


_status_command(
    "close", "closed", "Close answered queries (when the user says the answer settles it)."
)


@query_app.command("withdraw")
def query_withdraw(
    company: CompanyArg,
    ids: QueryIds,
    reason: Annotated[
        str, typer.Option("--reason", callback=text_option, help="Why it is dropped.")
    ],
) -> None:
    """Withdraw queries that are not closed or withdrawn already."""

    show(
        decisions.set_query_status(
            data_dir(),
            company,
            ids,
            "withdrawn",
            via="cli",
            actor=from_environment(),
            reason=reason,
        )
    )


# fin query export (export_cli.py)
export_cli.register(query_app, CompanyArg)
inquiry_cli.register(query_app, CompanyArg)


gate_app = typer.Typer(
    no_args_is_help=True,
    help="Diligence gates: fin checks each one's checklist; only the user approves a gate.",
)


def _gate(value: str) -> str:
    name = value.upper()
    if name not in gates.NAMES:
        raise typer.BadParameter(f"use one of: {', '.join(gates.NAMES)}")
    return name


@gate_app.command("status")
def gate_status(company: CompanyArg, json_output: JsonOption = False) -> None:
    """Each gate in order: approved, reopened (and why) or open; its checklist; what it unlocks."""

    def body(run: Run) -> None:
        result = gates.gate_status(run, company)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(gates.format_status(result))

    execute("gate status", {"company": company, "json": json_output}, body)


@gate_app.command("approve")
def gate_approve(
    company: CompanyArg,
    gate: Annotated[str, typer.Argument(callback=_gate, help="The gate, e.g. G1.")],
) -> None:
    """Approve a gate (only when the user says so). Refused (E802) while its checklist fails or an
    earlier gate is not approved."""

    show(decisions.approve_gate(data_dir(), company, gate, via="cli", actor=from_environment()))
