"""`fin engagement open|status|close|bridge|passthrough`,
`fin query list|raise|edit|approve|sent|answer|close|withdraw` and `fin gate status|approve`
(`fin adjust` is in `adjust_cli.py`). The logic is in `engagement.py`, `queries.py`, `gates.py`,
`bridge.py` and `passthrough.py`. fin never sends a query: `fin query sent`
records that the user sent it."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.config import SCOPES
from fin.diligence import (
    answers,
    bridge,
    engagement,
    export_cli,
    gates,
    inquiries,
    inquiry_cli,
    passthrough,
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
    periods_option,
    rupees_option,
    stub_option,
    text_option,
    threshold_option,
    tokens_option,
)
from fin.paths import data_dir
from fin.reconcile import items as recon_items
from fin.services import decisions
from fin.services.result import show

engagement_app = typer.Typer(
    no_args_is_help=True, help="Diligence engagement: open one, show where it stands, close it."
)
query_app = typer.Typer(
    no_args_is_help=True,
    help="Accountant queries: Claude drafts, the user approves and sends; fin never sends.",
)


# fin engagement


@engagement_app.command("open")
def engagement_open(
    company: CompanyArg,
    periods: Annotated[
        str,
        typer.Option(
            "--periods",
            callback=periods_option,
            help="The annual periods in scope, comma-separated, e.g. FY24,FY25,FY26.",
        ),
    ],
    threshold: Annotated[
        Decimal | None,
        typer.Option(
            "--threshold",
            parser=threshold_option,
            metavar="RUPEES",
            help="Query threshold in rupees. Default: query_threshold in config.toml.",
        ),
    ] = None,
    stub: Annotated[
        str | None,
        typer.Option(
            "--stub",
            callback=stub_option,
            help="Current-year stub after the last period, e.g. FY27-YTD-SEP (unaudited).",
        ),
    ] = None,
) -> None:
    """Open a diligence engagement for the company (one at a time)."""

    def body(run: Run) -> None:
        row = engagement.open_engagement(run, company, periods.split(","), threshold, stub)
        typer.echo(f"{company}: {engagement.format_engagement(row)}")

    args = {"company": company, "periods": periods, "threshold": threshold, "stub": stub}
    execute("engagement open", args, body)


@engagement_app.command("status")
def engagement_status(company: CompanyArg, json_output: JsonOption = False) -> None:
    """The open engagement: queries by status, adjustments by status with their sums."""

    def body(run: Run) -> None:
        result = engagement.engagement_status(run, company)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(engagement.format_status(result))

    execute("engagement status", {"company": company, "json": json_output}, body)


@engagement_app.command("close")
def engagement_close(company: CompanyArg) -> None:
    """Close the open engagement. Its queries and adjustments are kept."""

    def body(run: Run) -> None:
        row = engagement.close_engagement(run, company)
        typer.echo(f"{company}: {engagement.format_engagement(row)}")

    execute("engagement close", {"company": company}, body)


@engagement_app.command("bridge")
def engagement_bridge(
    company: CompanyArg,
    period: Annotated[
        str | None,
        typer.Option("--period", callback=period_option, help="Only this period, e.g. FY25."),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope", callback=choice_of(SCOPES), help="Only standalone or consolidated."
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print JSON rows, in rupees.")
    ] = False,
) -> None:
    """The latest engagement's EBITDA bridge (reported EBITDA, accepted adjustments, adjusted
    EBITDA) and EBITDA-to-cash walk, per period and scope. Money in the documents' unit, as
    `fin statements`."""

    def body(run: Run) -> None:
        result = bridge.bridge(run, company, period, scope)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(bridge.format_bridge(result))

    args = {"company": company, "period": period, "scope": scope, "json": json_output}
    execute("engagement bridge", args, body)


@engagement_app.command("passthrough")
def engagement_passthrough(
    company: CompanyArg,
    customer: Annotated[
        list[str] | None,
        typer.Option(
            "--customer",
            callback=tokens_option,
            help="A pass-through customer token (CUST-0001); repeat for each. Replaces the list.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", callback=text_option, help="Where the list came from (with --customer)."
        ),
    ] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Empty the list.")] = False,
    json_output: JsonOption = False,
) -> None:
    """The user's pass-through customers (SI/reseller deals at about zero margin) and the revenue
    split they cause. With --customer, set the list (the user's list; --source says where from);
    without, show it."""
    if customer and clear:
        raise typer.BadParameter("give --customer or --clear, not both")
    if customer and source is None:
        raise typer.BadParameter("--customer needs --source: where the user's list came from")

    def body(run: Run) -> None:
        if customer or clear:
            result = passthrough.set_customers(run, company, customer or [], source)
        else:
            result = passthrough.show(run, company)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            typer.echo(passthrough.format_show(result))

    args = {
        "company": company,
        "customer": customer,
        "source": source,
        "clear": clear,
        "json": json_output,
    }
    execute("engagement passthrough", args, body)


# fin query


def _echo_queries(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        typer.echo(queries.format_query(row))


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
) -> None:
    """Raise a draft query: from a finding (its period, scope, difference and sources; the finding
    is not changed), from reconciliation lines (--items: one issue, many lines), as an inquiry
    for management (--topic), or with --amount, --period and --source."""

    # Usage errors, before the run starts (like the option callbacks).
    item_ids = check_raise_options(items, finding, (amount, period, scope, source), topic)

    def body(run: Run) -> None:
        if topic is not None:
            row = inquiries.raise_inquiry(
                run, company, topic=topic, question=question, period=period, scope=scope
            )
            typer.echo(queries.format_query(row))
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

    def body(run: Run) -> None:
        typer.echo(queries.format_query(queries.edit(run, company, query_id, question)))

    execute("query edit", {"company": company, "id": query_id, "question": question}, body)


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

    def body(run: Run) -> None:
        answer, answer_source = text, source
        if file is not None:
            answer, from_file = answers.record_reply(run, company, file, text)
            answer_source = f"{source}; {from_file}" if source else from_file
        rows = queries.set_status(
            run, company, [query_id], "answered", answer=answer, answer_source=answer_source
        )
        _echo_queries(rows)

    args = {"company": company, "id": query_id, "text": text, "source": source, "file": file}
    execute("query answer", args, body)


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
