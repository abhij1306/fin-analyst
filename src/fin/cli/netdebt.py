"""`fin netdebt` (docs/fdd.md F4): net debt and debt-like items at each period end
(`fin.diligence.netdebt`, `fin.diligence.netdebt_items`). Claude proposes ledger classes (the user
confirms them in `fin review ... --netdebt`) and debt-like items (the user accepts or rejects them
here). Nothing here changes company data."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.audit.errors import NetDebtClassInvalid
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import netdebt, netdebt_items
from fin.diligence.engagement import parse_rupees

netdebt_app = typer.Typer(
    no_args_is_help=True, help="FDD net debt: cash, debt and debt-like items per period end."
)
item_app = typer.Typer(
    no_args_is_help=True, help="Debt-like items not in a ledger: Claude proposes, the user decides."
)
app.add_typer(netdebt_app, name="netdebt")
netdebt_app.add_typer(item_app, name="item")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]
Ids = Annotated[list[int], typer.Argument(help="Item ids (`fin netdebt item list`).")]
Json = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _rupees(value: str) -> Decimal:
    try:
        return parse_rupees(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _echo(rows: list[dict[str, Any]], json_output: bool, line: Any, empty: str) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(line, rows)) if rows else empty)


@netdebt_app.command("show")
def show(
    company: Company,
    period: Annotated[str | None, typer.Option("--period", help="Only this period.")] = None,
    json_output: Json = False,
) -> None:
    """Net debt at each period end of the latest engagement (views net_debt, net_debt_lines):
    cash, debt and debt-like ledgers from the trial balance, accepted debt-like items, and the
    proposed items shown, not applied. --json in rupees."""

    def body(run: Run) -> None:
        data = netdebt.show(run, company, period)
        if json_output:
            typer.echo(json.dumps(data, indent=2, default=str))
        else:
            typer.echo(netdebt.format_show(data))

    execute("netdebt show", {"company": company, "period": period}, body)


def _unclassified(row: dict[str, Any]) -> str:
    line = f"{row['ledger']!r} in {row['group_name'] or '-'} (account {row['account'] or '-'})"
    if row["proposal_id"] is not None:
        line += f": proposed #{row['proposal_id']} as {row['proposed_class']}"
    return line


@netdebt_app.command("propose")
def propose(
    company: Company,
    file: Annotated[
        Path | None, typer.Argument(help="JSON list of {ledger, class, reason}.")
    ] = None,
) -> None:
    """Without FILE: list the trial balance ledgers with no net debt class. With FILE: store
    Claude's class proposals (class: cash, debt, debt_like or none); the user confirms or rejects
    them (`fin review confirm|reject <co> <ids> --netdebt`)."""

    def body(run: Run) -> None:
        if file is None:
            rows = netdebt.unclassified(run, company)
            _echo(rows, False, _unclassified, "every ledger has a class")
            return
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NetDebtClassInvalid(f"cannot read proposals from {file}: {exc}") from exc
        ids = netdebt.propose(run, company, proposals)
        typer.echo(
            f"{len(ids)} net debt classes proposed for {company} (#{', #'.join(map(str, ids))}); "
            f"the user reviews them: `fin review list {company} --netdebt`"
        )

    execute("netdebt propose", {"company": company, "file": file and str(file)}, body)


@item_app.command("propose")
def item_propose(
    company: Company,
    period: Annotated[str, typer.Option("--period", help="A year of the engagement or its stub.")],
    amount: Annotated[
        Decimal,
        typer.Option(
            "--amount",
            parser=_rupees,
            metavar="RUPEES",
            help="Signed rupees; positive raises net debt.",
        ),
    ],
    description: Annotated[str, typer.Option("--description", help="What the item is.")],
    evidence: Annotated[
        str, typer.Option("--evidence", help="A reference: query, fin output, file + page.")
    ],
) -> None:
    """Propose a debt-like item not in a ledger (an indicative gratuity shortfall, a probable
    contingency, a cut-off item). It counts in net debt only once the user accepts it."""

    def body(run: Run) -> None:
        row = netdebt_items.propose(run, company, period, amount, description, evidence)
        typer.echo(netdebt_items.format_item(row))

    args = {"company": company, "period": period, "amount": str(amount)}
    execute("netdebt item propose", args, body)


@item_app.command("list")
def item_list(
    company: Company,
    status: Annotated[
        str | None, typer.Option("--status", help="proposed, accepted or rejected.")
    ] = None,
    json_output: Json = False,
) -> None:
    """The open engagement's debt-like items."""
    execute(
        "netdebt item list",
        {"company": company, "status": status},
        lambda run: _echo(
            netdebt_items.list_items(run, company, status),
            json_output,
            netdebt_items.format_item,
            "no net debt items",
        ),
    )


@item_app.command("accept")
def item_accept(company: Company, ids: Ids) -> None:
    """Accept proposed items: they count in net debt. Only when the user says so."""
    execute(
        "netdebt item accept",
        {"company": company, "ids": ids},
        lambda run: _echo(
            netdebt_items.decide(run, company, ids, "accepted"),
            False,
            netdebt_items.format_item,
            "",
        ),
    )


@item_app.command("reject")
def item_reject(
    company: Company,
    ids: Ids,
    reason: Annotated[str, typer.Option("--reason", help="Why it is rejected.")],
) -> None:
    """Reject proposed items, with the reason. Only when the user says so."""
    execute(
        "netdebt item reject",
        {"company": company, "ids": ids, "reason": reason},
        lambda run: _echo(
            netdebt_items.decide(run, company, ids, "rejected", reason),
            False,
            netdebt_items.format_item,
            "",
        ),
    )
