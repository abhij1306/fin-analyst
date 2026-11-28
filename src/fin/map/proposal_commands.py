"""`fin map ledgers|customers`: list what waits for Claude's proposals, or store them (split from
`fin.map.commands`, which has the review side: `fin review ... --ledgers|--customers`)."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run, execute
from fin.diligence import customers, ledgers
from fin.diligence.customers import list_customers
from fin.diligence.ledgers import list_ledgers
from fin.map.commands import CompanyArg, map_app
from fin.map.review_commands import _echo_customers, _echo_ledgers


@map_app.command("ledgers")
def map_ledgers(
    company: CompanyArg,
    file: Annotated[
        Path | None,
        typer.Argument(help="JSON list of proposals: {ledger, account, confidence, reason}."),
    ] = None,
) -> None:
    """Tally ledgers -> Schedule III accounts. Without FILE: list the ledgers waiting for an
    account. With FILE: store Claude's proposals; nothing is confirmed (the user confirms them:
    `fin review confirm <co> <ids> --ledgers`)."""

    def body(run: Run) -> None:
        if file is None:
            _echo_ledgers(list_ledgers(run, company, True), False)
            return
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidMapping(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise InvalidMapping(f"{file}: expected a JSON list of proposals")
        count = ledgers.propose(run, company, proposals)
        typer.echo(
            f"{count} ledger proposals stored for {company}; the user reviews them: "
            f"`fin review list {company} --ledgers`"
        )

    execute("map ledgers", {"company": company, "file": file}, body)


@map_app.command("customers")
def map_customers(
    company: CompanyArg,
    file: Annotated[
        Path | None,
        typer.Argument(help="JSON list of proposals: {customer, sector, confidence, reason}."),
    ] = None,
) -> None:
    """Customer sectors, for the sector mix. Without FILE: list the customers (tokens) waiting for a
    sector. With FILE: store Claude's proposals; nothing is confirmed (the user confirms them:
    `fin review confirm <co> <ids> --customers`)."""

    def body(run: Run) -> None:
        if file is None:
            _echo_customers(list_customers(run, company, True), False)
            return
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidMapping(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise InvalidMapping(f"{file}: expected a JSON list of proposals")
        count = customers.propose(run, company, proposals)
        typer.echo(
            f"{count} customer sector proposals stored for {company}; the user reviews them: "
            f"`fin review list {company} --customers`"
        )

    execute("map customers", {"company": company, "file": file}, body)
