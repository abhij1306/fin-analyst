"""`fin provisions` (docs/fdd.md F3): receivables ageing against the provision for doubtful debts,
indicative gratuity against the gratuity provision, and the provision ledgers behind both
(`fin.diligence.provisions`). Claude proposes provision ledgers; only the user confirms or rejects
them. Nothing here changes company data."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.actor import from_environment
from fin.audit.errors import ProvisionLedgerInvalid
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import provisions
from fin.diligence.engagement import rupees
from fin.paths import data_dir
from fin.services import decisions
from fin.services.result import show as show_result

provisions_app = typer.Typer(
    no_args_is_help=True,
    help="FDD provisions: receivables ageing, indicative gratuity, provision ledgers.",
)
app.add_typer(provisions_app, name="provisions")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]
Ids = Annotated[list[int], typer.Argument(help="Provision ledger ids (`fin provisions ledgers`).")]


def _percent(value: Any) -> str:
    return "-" if value is None else f"{Decimal(value):.1%}"


def format_show(data: dict[str, Any]) -> str:
    out = ["Receivables ageing at the year end (open sales invoices, from bank_vs_invoices)"]
    for r in data["receivables"]:
        out.append(
            f"  {r['period']} {r['bucket']:>8}: open {rupees(r['open_inr'])} "
            f"({r['invoices']} invoices, {_percent(r['share'])}), provision "
            f"{rupees(r['provision_inr'])}, coverage {_percent(r['coverage'])}"
        )
        if r["bucket"] == "total" and r["not_in_bank_run_inr"]:
            out.append(
                f"    not aged: {rupees(r['not_in_bank_run_inr'])} of sales invoices are in no "
                "bank_vs_invoices run (no bank statement for their period)"
            )
    if not data["receivables"]:
        out.append("  none: run `fin reconcile <company> bank_vs_invoices` first")
    a = data["assumptions"]
    out.append(
        f"Gratuity ({a['label']}; wages = gross salary x {a['basic_share']}, config.toml [fdd] "
        f"basic_share; at least {a['gratuity_min_years']} years; cap {rupees(a['gratuity_cap'])})"
    )
    for g in data["gratuity"]:
        out.append(
            f"  {g['period']} at {g['snapshot_date']}: {g['eligible']} of {g['employees']} "
            f"employees eligible ({g['incomplete']} incomplete), indicative "
            f"{rupees(g['gratuity_basic_share'])}, wage-code {rupees(g['gratuity_wage_code'])}; "
            f"provision {rupees(g['provision_inr'])}; shortfall "
            f"{rupees(g['shortfall_basic_share'])} (wage-code {rupees(g['shortfall_wage_code'])})"
        )
    if not data["gratuity"]:
        out.append("  none: no payroll snapshot with its unit confirmed annual")
    for r in data["absent"]:
        where = f"the trial balance {r['trial_balance_file']}"
        out.append(
            f"Warning: {r['period']} {r['kind']} provision unknown ('-', not zero): no confirmed "
            f"{r['kind']} ledger is in {where}"
            if not r["ledgers"]
            else f"Warning: {r['period']} {r['kind']} provision has {r['ledgers']} of the "
            f"{r['confirmed_ledgers']} confirmed {r['kind']} ledgers: the others are not in {where}"
        )
    waiting = [r for r in data["ledgers"] if r["status"] == "proposed"]
    if not any(r["status"] == "confirmed" for r in data["ledgers"]):
        out.append("No provision ledger is confirmed: provision figures show '-'")
    if waiting:
        out.append(f"{len(waiting)} provision ledgers wait for the user (`fin provisions ledgers`)")
    return "\n".join(out)


def _echo(rows: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(provisions.format_ledger, rows)) or "no provision ledgers")


@provisions_app.command("show")
def show(
    company: Company,
    period: Annotated[
        str | None, typer.Option("--period", help="Only this year, e.g. FY25.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Receivables ageing at each year end against the provision for doubtful debts, and the
    indicative gratuity (undiscounted, on the assumptions in config.toml's fdd table) against the
    gratuity provision. A shortfall is for a query or a net debt item, never a fix."""

    def body(run: Run) -> None:
        data = provisions.show(run, company, period)
        if json_output:
            typer.echo(json.dumps(data, indent=2, default=str))
        else:
            typer.echo(format_show(data))

    execute("provisions show", {"company": company, "period": period}, body)


@provisions_app.command("ledgers")
def ledgers(
    company: Company,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the provision ledgers: proposed, confirmed and rejected."""
    execute(
        "provisions ledgers",
        {"company": company},
        lambda run: _echo(provisions.list_ledgers(run, company), json_output),
    )


@provisions_app.command("propose")
def propose(
    company: Company,
    file: Annotated[Path, typer.Argument(help="JSON list of {ledger, kind, reason}.")],
) -> None:
    """Store Claude's proposals of the ledgers that hold the provision for doubtful debts
    (kind doubtful_debts) or the gratuity provision (gratuity). The user reviews them."""

    def body(run: Run) -> None:
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProvisionLedgerInvalid(f"cannot read proposals from {file}: {exc}") from exc
        ids = provisions.propose(run, company, proposals)
        typer.echo(
            f"{len(ids)} provision ledgers proposed for {company} (#{', #'.join(map(str, ids))}); "
            f"the user confirms or rejects them: `fin provisions ledgers {company}`"
        )

    execute("provisions propose", {"company": company, "file": str(file)}, body)


@provisions_app.command("confirm")
def confirm(company: Company, ids: Ids) -> None:
    """Confirm proposed provision ledgers. Only when the user says so."""
    show_result(
        decisions.decide_provision_ledgers(
            data_dir(), company, ids, "confirmed", via="cli", actor=from_environment()
        )
    )


@provisions_app.command("reject")
def reject(company: Company, ids: Ids) -> None:
    """Reject proposed or confirmed provision ledgers. Only when the user says so."""
    show_result(
        decisions.decide_provision_ledgers(
            data_dir(), company, ids, "rejected", via="cli", actor=from_environment()
        )
    )
