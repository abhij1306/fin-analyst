"""`fin adjust propose|import|list|accept|reject`: adjustments to EBITDA with evidence. The logic is
in `adjustments.py` and `adjust_import.py`."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.config import SCOPES
from fin.diligence import adjustments, engagement
from fin.diligence.cli_options import (
    AdjustmentIds,
    CompanyArg,
    JsonOption,
    choice_of,
    echo_rows,
    rupees_option,
    text_option,
)
from fin.paths import data_dir
from fin.services import decisions
from fin.services import diligence as services
from fin.services.result import show

adjust_app = typer.Typer(
    no_args_is_help=True,
    help="Adjustments to EBITDA, with evidence: Claude proposes, the user accepts or rejects.",
)


@adjust_app.command("propose")
def adjust_propose(
    company: CompanyArg,
    period: Annotated[
        str, typer.Option("--period", help="An annual period of the engagement, e.g. FY25.")
    ],
    scope: Annotated[
        str, typer.Option("--scope", callback=choice_of(SCOPES), help="standalone or consolidated.")
    ],
    category: Annotated[
        str, typer.Option("--category", help="One of adjustment_categories in config.toml.")
    ],
    description: Annotated[
        str, typer.Option("--description", callback=text_option, help="What it is and why.")
    ],
    amount: Annotated[
        Decimal | None,
        typer.Option(
            "--amount",
            parser=rupees_option,
            metavar="RUPEES",
            help="Signed effect on EBITDA (+ raises it). Alone, it is a cost effect of minus this.",
        ),
    ] = None,
    revenue_effect: Annotated[
        Decimal | None,
        typer.Option(
            "--revenue-effect",
            parser=rupees_option,
            metavar="RUPEES",
            help="Signed effect on revenue. The EBITDA effect is revenue effect - cost effect.",
        ),
    ] = None,
    cost_effect: Annotated[
        Decimal | None,
        typer.Option(
            "--cost-effect",
            parser=rupees_option,
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
            parser=rupees_option,
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
            "--source", callback=text_option, help="Evidence: file + sheet!cell or page reference."
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


@adjust_app.command("import")
def adjust_import(
    company: CompanyArg,
    file: Annotated[Path, typer.Argument(help="The walk sheet: a .csv, .xlsx or .xlsm file.")],
    sheet: Annotated[
        str | None,
        typer.Option("--sheet", callback=text_option, help="The Excel sheet. Default: the first."),
    ] = None,
) -> None:
    """Propose one adjustment per row of a walk sheet: period, scope, category, account, revenue
    effect, cost effect, description, source reference. Each keeps the file and cell as evidence.
    A bad row refuses the whole file (E847) and names the row."""

    show(
        services.import_adjustments(
            data_dir(), company, file, sheet, via="cli", actor=from_environment()
        )
    )


@adjust_app.command("list")
def adjust_list(
    company: CompanyArg,
    status: Annotated[
        str | None,
        typer.Option(callback=choice_of(engagement.ADJUSTMENT_STATUSES), help="Only this status."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """The open engagement's adjustments, with their evidence."""

    def body(run: Run) -> None:
        if not engagement.is_open(run, company):
            typer.echo("[]" if json_output else f"{company} has no open engagement: no adjustments")
            return
        rows = adjustments.list_adjustments(run, company, status)
        echo_rows(rows, json_output, adjustments.format_adjustment, "no adjustments")

    execute("adjust list", {"company": company, "status": status, "json": json_output}, body)


@adjust_app.command("accept")
def adjust_accept(company: CompanyArg, ids: AdjustmentIds) -> None:
    """Accept proposed adjustments (when the user accepts them)."""

    show(
        decisions.decide_adjustments(
            data_dir(), company, ids, "accepted", via="cli", actor=from_environment()
        )
    )


@adjust_app.command("reject")
def adjust_reject(
    company: CompanyArg,
    ids: AdjustmentIds,
    reason: Annotated[
        str, typer.Option("--reason", callback=text_option, help="Why it is rejected.")
    ],
) -> None:
    """Reject proposed adjustments (when the user rejects them), with the reason."""

    show(
        decisions.decide_adjustments(
            data_dir(), company, ids, "rejected", via="cli", actor=from_environment(), reason=reason
        )
    )


# fin gate (logic in gates.py)
