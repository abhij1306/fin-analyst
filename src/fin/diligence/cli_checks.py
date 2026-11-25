"""Option checks of `fin query raise` (see fin.diligence.cli)."""

from typing import Any

import typer

from fin.reconcile import items as recon_items


def check_raise_options(
    items: str | None,
    finding: int | None,
    manual: tuple[Any, Any, Any, Any],
    topic: str | None = None,
) -> list[int]:
    """The item ids of `query raise --items` ([] otherwise); BadParameter for a bad mix of the
    options. `manual` is (amount, period, scope, source)."""
    amount, period, _, source = manual
    if topic is not None:
        if items is not None or finding is not None or amount is not None or source is not None:
            raise typer.BadParameter(
                "--topic raises an inquiry (no amount or source); do not give --finding, "
                "--items, --amount or --source too"
            )
        return []
    if items is not None:
        if finding is not None or any(value is not None for value in manual):
            raise typer.BadParameter(
                "--items takes the period, amount and sources from the lines; do not give "
                "--finding, --amount, --period, --scope or --source too"
            )
        try:
            return recon_items.parse_ids(items)
        except ValueError:
            raise typer.BadParameter("give item ids separated by commas, e.g. 12,13,14") from None
    if finding is not None:
        if any(value is not None for value in manual):
            raise typer.BadParameter(
                "--finding takes the amount, period, scope and sources from the finding; "
                "do not give them too"
            )
        return []
    if amount is None or period is None or source is None:
        raise typer.BadParameter("give --finding, --items, or --amount, --period and --source")
    return []
