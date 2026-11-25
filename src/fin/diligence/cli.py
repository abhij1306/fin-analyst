"""`fin engagement open|status|close|bridge|passthrough`,
`fin query list|raise|edit|approve|sent|answer|close|withdraw` and
`fin adjust propose|list|accept|reject`. The logic is in `engagement.py`, `queries.py`,
`adjustments.py`, `bridge.py` and `passthrough.py`. fin never sends a query: `fin query sent`
records that the user sent it."""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.audit.logger import Run, execute
from fin.config import SCOPES
from fin.diligence import (
    adjustments,
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
from fin.diligence.stub import STUB
from fin.paths import COMPANY_NAME
from fin.reconcile import items as recon_items

engagement_app = typer.Typer(
    no_args_is_help=True, help="Diligence engagement: open one, show where it stands, close it."
)
query_app = typer.Typer(
    no_args_is_help=True,
    help="Accountant queries: Claude drafts, the user approves and sends; fin never sends.",
)
adjust_app = typer.Typer(
    no_args_is_help=True,
    help="Adjustments to EBITDA, with evidence: Claude proposes, the user accepts or rejects.",
)


def _company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


def _rupees(value: str) -> Decimal:
    try:
        return engagement.parse_rupees(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _threshold(value: str) -> Decimal:
    amount = _rupees(value)
    if amount < 0:
        raise typer.BadParameter("the threshold must be 0 or more rupees")
    return amount


def _periods(value: str) -> str:
    """The periods in order, comma-separated: 'FY26,FY24' -> 'FY24,FY26'."""
    try:
        return ",".join(engagement.parse_periods(value))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _stub(value: str | None) -> str | None:
    if value is not None and not STUB.match(value.strip().upper()):
        raise typer.BadParameter(
            "name the stub by its fiscal year and end month, e.g. FY27-YTD-SEP"
        )
    return None if value is None else value.strip().upper()


def _period(value: str | None) -> str | None:
    if value is not None and not engagement.PERIOD.match(value):
        raise typer.BadParameter("use a period such as FY25, FY26-Q1, FY26-H1 or FY26-9M")
    return value


def _text(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise typer.BadParameter("must not be empty")
    return None if value is None else value.strip()


def _tokens(values: list[str] | None) -> list[str] | None:
    if values is not None and not all(re.fullmatch(r"[A-Z]+-\d+", v.strip()) for v in values):
        raise typer.BadParameter(
            "a customer is a token such as CUST-0001 (letters, a dash, digits)"
        )
    return None if values is None else [v.strip() for v in values]


def _choice(choices: tuple[str, ...]) -> Any:
    def check(value: str | None) -> str | None:
        if value is not None and value not in choices:
            raise typer.BadParameter(f"use one of: {', '.join(choices)}")
        return value

    return check


def _echo(rows: list[dict[str, Any]], json_output: bool, line: Any, empty: str) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(line, rows)) if rows else empty)


CompanyArg = Annotated[
    str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.", show_default=False)
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]
QueryIds = Annotated[list[int], typer.Argument(help="Query ids from `fin query list`.")]
AdjustmentIds = Annotated[list[int], typer.Argument(help="Adjustment ids from `fin adjust list`.")]


# fin engagement


@engagement_app.command("open")
def engagement_open(
    company: CompanyArg,
    periods: Annotated[
        str,
        typer.Option(
            "--periods",
            callback=_periods,
            help="The annual periods in scope, comma-separated, e.g. FY24,FY25,FY26.",
        ),
    ],
    threshold: Annotated[
        Decimal | None,
        typer.Option(
            "--threshold",
            parser=_threshold,
            metavar="RUPEES",
            help="Query threshold in rupees. Default: query_threshold in config.toml.",
        ),
    ] = None,
    stub: Annotated[
        str | None,
        typer.Option(
            "--stub",
            callback=_stub,
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
        str | None, typer.Option("--period", callback=_period, help="Only this period, e.g. FY25.")
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option("--scope", callback=_choice(SCOPES), help="Only standalone or consolidated."),
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
            callback=_tokens,
            help="A pass-through customer token (CUST-0001); repeat for each. Replaces the list.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", callback=_text, help="Where the list came from (with --customer)."
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
        typer.Option(callback=_choice(engagement.QUERY_STATUSES), help="Only this status."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """The open engagement's queries, with amounts, sources and answers."""

    def body(run: Run) -> None:
        rows = queries.list_queries(run, company, status)
        _echo(rows, json_output, queries.format_query, "no queries")

    execute("query list", {"company": company, "status": status, "json": json_output}, body)


@query_app.command("raise")
def query_raise(
    company: CompanyArg,
    question: Annotated[
        str, typer.Option("--question", callback=_text, help="The question for the accountant.")
    ],
    finding: Annotated[
        int | None,
        typer.Option("--finding", help="Raise it from this finding (`fin findings list`)."),
    ] = None,
    amount: Annotated[
        Decimal | None,
        typer.Option(
            "--amount",
            parser=_rupees,
            metavar="RUPEES",
            help="Without --finding: the difference.",
        ),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period", callback=_period, help="Without --finding: the period, e.g. FY25."
        ),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope",
            callback=_choice(SCOPES),
            help="Without --finding: standalone or consolidated.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            callback=_text,
            help="Without --finding: where the figures are (file + sheet!cell or page).",
        ),
    ] = None,
    topic: Annotated[
        str | None,
        typer.Option(
            "--topic",
            callback=_text,
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
    question: Annotated[str, typer.Option("--question", callback=_text, help="The new wording.")],
) -> None:
    """Change the wording of a draft query."""

    def body(run: Run) -> None:
        typer.echo(queries.format_query(queries.edit(run, company, query_id, question)))

    execute("query edit", {"company": company, "id": query_id, "question": question}, body)


def _status_command(name: str, target: str, doc: str) -> None:
    """`fin query <name> <co> <ids...>`: move queries to `target`."""

    def command(company: CompanyArg, ids: QueryIds) -> None:
        def body(run: Run) -> None:
            _echo_queries(queries.set_status(run, company, ids, target))

        execute(f"query {name}", {"company": company, "ids": ids}, body)

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
        str | None, typer.Option("--text", callback=_text, help="The accountant's answer.")
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", callback=_text, help="Where the answer is (a file, an email)."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="The reply file: archived like a source. A .txt, .eml or .md reply is read "
            "(known names masked) and is the answer; any other file needs --text.",
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
    reason: Annotated[str, typer.Option("--reason", callback=_text, help="Why it is dropped.")],
) -> None:
    """Withdraw queries that are not closed or withdrawn already."""

    def body(run: Run) -> None:
        _echo_queries(queries.set_status(run, company, ids, "withdrawn", note=reason))

    execute("query withdraw", {"company": company, "ids": ids, "reason": reason}, body)


# fin query export (export_cli.py)
export_cli.register(query_app, CompanyArg)
inquiry_cli.register(query_app, CompanyArg)


# fin adjust


@adjust_app.command("propose")
def adjust_propose(
    company: CompanyArg,
    period: Annotated[
        str, typer.Option("--period", help="An annual period of the engagement, e.g. FY25.")
    ],
    scope: Annotated[
        str, typer.Option("--scope", callback=_choice(SCOPES), help="standalone or consolidated.")
    ],
    category: Annotated[
        str, typer.Option("--category", help="One of adjustment_categories in config.toml.")
    ],
    description: Annotated[
        str, typer.Option("--description", callback=_text, help="What it is and why.")
    ],
    amount: Annotated[
        Decimal | None,
        typer.Option(
            "--amount",
            parser=_rupees,
            metavar="RUPEES",
            help="Signed effect on EBITDA (+ raises it). Alone, it is a cost effect of minus this.",
        ),
    ] = None,
    revenue_effect: Annotated[
        Decimal | None,
        typer.Option(
            "--revenue-effect",
            parser=_rupees,
            metavar="RUPEES",
            help="Signed effect on revenue. The EBITDA effect is revenue effect - cost effect.",
        ),
    ] = None,
    cost_effect: Annotated[
        Decimal | None,
        typer.Option(
            "--cost-effect",
            parser=_rupees,
            metavar="RUPEES",
            help="Signed effect on costs (+ raises them); stripping costs is negative.",
        ),
    ] = None,
    account: Annotated[
        str | None,
        typer.Option(
            "--account", help="The dictionary account it relates to (the cost moves to it)."
        ),
    ] = None,
    from_account: Annotated[
        str | None,
        typer.Option(
            "--from-account", help="Reclassification between cost accounts: the account it leaves."
        ),
    ] = None,
    moved_amount: Annotated[
        Decimal | None,
        typer.Option(
            "--moved-amount",
            parser=_rupees,
            metavar="RUPEES",
            help="With --from-account: the positive amount moved. Its effects are all 0.",
        ),
    ] = None,
    query: Annotated[int | None, typer.Option("--query", help="Evidence: a query id.")] = None,
    finding: Annotated[
        int | None, typer.Option("--finding", help="Evidence: a finding id.")
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", callback=_text, help="Evidence: file + sheet!cell or page reference."
        ),
    ] = None,
) -> None:
    """Propose an adjustment to EBITDA, revenue and costs (the EBITDA effect is revenue effect minus
    cost effect). It needs evidence: --query, --finding or --source."""

    def body(run: Run) -> None:
        row = adjustments.propose(
            run,
            company,
            period=period,
            scope=scope,
            category=category,
            amount=amount,
            revenue_effect=revenue_effect,
            cost_effect=cost_effect,
            description=description,
            account=account,
            from_account=from_account,
            moved_amount=moved_amount,
            evidence=adjustments.Evidence(query_id=query, finding_id=finding, source_ref=source),
        )
        typer.echo(adjustments.format_adjustment(row))

    args = {
        "company": company,
        "period": period,
        "scope": scope,
        "category": category,
        "amount": amount,
        "revenue_effect": revenue_effect,
        "cost_effect": cost_effect,
        "description": description,
        "account": account,
        "from_account": from_account,
        "moved_amount": moved_amount,
        "query": query,
        "finding": finding,
        "source": source,
    }
    execute("adjust propose", args, body)


@adjust_app.command("list")
def adjust_list(
    company: CompanyArg,
    status: Annotated[
        str | None,
        typer.Option(callback=_choice(engagement.ADJUSTMENT_STATUSES), help="Only this status."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """The open engagement's adjustments, with their evidence."""

    def body(run: Run) -> None:
        rows = adjustments.list_adjustments(run, company, status)
        _echo(rows, json_output, adjustments.format_adjustment, "no adjustments")

    execute("adjust list", {"company": company, "status": status, "json": json_output}, body)


@adjust_app.command("accept")
def adjust_accept(company: CompanyArg, ids: AdjustmentIds) -> None:
    """Accept proposed adjustments (when the user accepts them)."""

    def body(run: Run) -> None:
        for row in adjustments.decide(run, company, ids, "accepted"):
            typer.echo(adjustments.format_adjustment(row))

    execute("adjust accept", {"company": company, "ids": ids}, body)


@adjust_app.command("reject")
def adjust_reject(
    company: CompanyArg,
    ids: AdjustmentIds,
    reason: Annotated[str, typer.Option("--reason", callback=_text, help="Why it is rejected.")],
) -> None:
    """Reject proposed adjustments (when the user rejects them), with the reason."""

    def body(run: Run) -> None:
        for row in adjustments.decide(run, company, ids, "rejected", reason):
            typer.echo(adjustments.format_adjustment(row))

    execute("adjust reject", {"company": company, "ids": ids, "reason": reason}, body)


# fin gate (logic in gates.py)

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

    def body(run: Run) -> None:
        typer.echo(f"{company}: {gates.format_gate(gates.approve(run, company, gate))}")

    execute("gate approve", {"company": company, "gate": gate}, body)
