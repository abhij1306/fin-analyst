"""`fin engagement open|status|close|terms|bridge|passthrough|rate`. The logic is in
`engagement.py`, `bridge.py`, `passthrough.py` and `rates.py`."""

import json
from decimal import Decimal
from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.config import SCOPES
from fin.diligence import bridge, engagement, passthrough, rates
from fin.diligence.cli_options import (
    CompanyArg,
    JsonOption,
    choice_of,
    period_option,
    periods_option,
    stub_option,
    text_option,
    threshold_option,
    tokens_option,
)

engagement_app = typer.Typer(
    no_args_is_help=True, help="Diligence engagement: open one, show where it stands, close it."
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
    due_days: Annotated[
        int | None,
        typer.Option(
            "--due-days",
            metavar="DAYS",
            help="Credit terms: an unmatched invoice is overdue after this many days. "
            "Default: invoice_due_days in config.toml (90).",
        ),
    ] = None,
) -> None:
    """Open a diligence engagement for the company (one at a time)."""

    def body(run: Run) -> None:
        row = engagement.open_engagement(
            run, company, periods.split(","), threshold, stub, due_days
        )
        typer.echo(f"{company}: {engagement.format_engagement(row)}")

    args = {
        "company": company,
        "periods": periods,
        "threshold": threshold,
        "stub": stub,
        "due_days": due_days,
    }
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


@engagement_app.command("terms")
def engagement_terms(
    company: CompanyArg,
    due_days: Annotated[
        int,
        typer.Option(
            "--due-days",
            metavar="DAYS",
            help="Credit terms: an unmatched invoice is overdue after this many days.",
        ),
    ],
) -> None:
    """Change the open engagement's credit terms. Overdue and ageing read them; gate G2 reopens
    until bank_vs_invoices runs again with them."""

    def body(run: Run) -> None:
        result = engagement.set_due_days(run, company, due_days)
        typer.echo(engagement.format_due_days(company, result))

    execute("engagement terms", {"company": company, "due_days": due_days}, body)


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


@engagement_app.command("rate")
def engagement_rate(
    company: CompanyArg,
    currency: Annotated[
        str | None, typer.Argument(help="The order currency, e.g. USD. Omit to list the rates.")
    ] = None,
    rate: Annotated[
        str | None, typer.Argument(help="Rupees per unit of the currency, e.g. 84.10.")
    ] = None,
    as_at: Annotated[
        str | None,
        typer.Option("--as-at", help="The date the rate is as at: the orderbook's, YYYY-MM-DD."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """State the exchange rate an order not in INR is valued at (the user's rate; fin never
    assumes one), or list the rates stated."""
    if currency is not None and (rate is None or as_at is None):
        raise typer.BadParameter(
            "give the currency, the rate and --as-at, e.g. USD 84.10 --as-at 2025-03-31"
        )

    def body(run: Run) -> None:
        if currency is None:
            rows = rates.list_rates(run, company)
            text = rates.format_rates(company, rows)
            result: object = rows
        else:
            assert rate is not None and as_at is not None
            result = rates.state_rate(run, company, currency, rate, as_at)
            text = rates.format_stated(result)
        typer.echo(json.dumps(result, indent=2, default=str) if json_output else text)

    args = {"company": company, "currency": currency, "rate": rate, "as_at": as_at}
    execute("engagement rate", args, body)
