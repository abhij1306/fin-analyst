"""`fin review list|confirm|reject <co> --netdebt`: the review side of FDD F4's net debt class
proposals (`fin.diligence.netdebt`), called from `fin.map.commands`. Only the user confirms or
rejects a class."""

import json
from typing import Any

import typer

from fin.audit.logger import Run
from fin.diligence import netdebt


def _echo(rows: list[dict[str, Any]], json_output: bool, empty: str = "") -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(netdebt.format_class, rows)) if rows else empty)


def list_classes(
    run: Run, companies: list[str], all_items: bool, json_output: bool, other_modes: bool
) -> None:
    """The class proposals of each company (only those waiting unless `all_items`)."""
    if other_modes:
        raise typer.BadParameter("--netdebt takes no other review mode")
    rows = [
        {"company": name, **row}
        for name in companies
        for row in netdebt.list_classes(run, name, not all_items)
    ]
    _echo(rows, json_output, "no net debt classes waiting")


def decide(run: Run, company: str, ids: list[int], status: str, other_options: bool) -> None:
    """Confirm or reject class proposals (E931), refusing any other review option."""
    if other_options:
        raise typer.BadParameter("--netdebt takes no other review option")
    _echo(netdebt.decide(run, company, ids, status), False)
