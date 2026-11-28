"""Option parsers and argument types shared by the diligence commands (`fin engagement`,
`fin query`, `fin adjust`, `fin gate`)."""

import json
import re
from decimal import Decimal
from typing import Annotated, Any

import typer

from fin.diligence import engagement
from fin.diligence.stub import STUB
from fin.paths import COMPANY_NAME


def valid_company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


def rupees_option(value: str) -> Decimal:
    try:
        return engagement.parse_rupees(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def threshold_option(value: str) -> Decimal:
    amount = rupees_option(value)
    if amount < 0:
        raise typer.BadParameter("the threshold must be 0 or more rupees")
    return amount


def periods_option(value: str) -> str:
    """The periods in order, comma-separated: 'FY26,FY24' -> 'FY24,FY26'."""
    try:
        return ",".join(engagement.parse_periods(value))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def stub_option(value: str | None) -> str | None:
    if value is not None and not STUB.match(value.strip().upper()):
        raise typer.BadParameter(
            "name the stub by its fiscal year and end month, e.g. FY27-YTD-SEP"
        )
    return None if value is None else value.strip().upper()


def period_option(value: str | None) -> str | None:
    if value is not None and not engagement.PERIOD.match(value):
        raise typer.BadParameter("use a period such as FY25, FY26-Q1, FY26-H1 or FY26-9M")
    return value


def text_option(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise typer.BadParameter("must not be empty")
    return None if value is None else value.strip()


def tokens_option(values: list[str] | None) -> list[str] | None:
    if values is not None and not all(re.fullmatch(r"[A-Z]+-\d+", v.strip()) for v in values):
        raise typer.BadParameter(
            "a customer is a token such as CUST-0001 (letters, a dash, digits)"
        )
    return None if values is None else [v.strip() for v in values]


def choice_of(choices: tuple[str, ...]) -> Any:
    def check(value: str | None) -> str | None:
        if value is not None and value not in choices:
            raise typer.BadParameter(f"use one of: {', '.join(choices)}")
        return value

    return check


def echo_rows(rows: list[dict[str, Any]], json_output: bool, line: Any, empty: str) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(line, rows)) if rows else empty)


CompanyArg = Annotated[
    str,
    typer.Argument(callback=valid_company, help="Company name, e.g. kestrel.", show_default=False),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]
QueryIds = Annotated[list[int], typer.Argument(help="Query ids from `fin query list`.")]
AdjustmentIds = Annotated[list[int], typer.Argument(help="Adjustment ids from `fin adjust list`.")]
