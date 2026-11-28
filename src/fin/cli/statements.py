"""`fin statements` and `fin export`."""

import json
from collections.abc import Callable
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company, check_scope
from fin.config import DISPLAY_UNITS, Display
from fin.databook import databook, format_databook
from fin.display import display_settings
from fin.paths import data_dir
from fin.rebuilt.compare import format_rebuilt, rebuilt_rows, select_period
from fin.services import reports
from fin.services.result import show
from fin.statements import format_statements, latest_annual_period, statement_rows

LAYOUTS = ("blocks", "databook")


def _one_of(choices: tuple[str, ...]) -> Callable[[str | None], str | None]:
    """A typer callback refusing a value not in `choices`."""

    def check(value: str | None) -> str | None:
        if value is not None and value not in choices:
            raise typer.BadParameter(f"{value!r}; use one of {', '.join(choices)}")
        return value

    return check


@app.command("statements")
def statements_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    period: Annotated[
        str | None,
        typer.Option("--period", help="FY25, FY26-Q1, ... Default: the latest fiscal year."),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope", callback=check_scope, help="standalone or consolidated. Default: both."
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON rows.")] = False,
    rebuilt: Annotated[
        bool,
        typer.Option(
            "--rebuilt",
            help="Reported, rebuilt from the trial balance and difference side by side "
            "(standalone).",
        ),
    ] = False,
    layout: Annotated[
        str,
        typer.Option(
            "--layout",
            callback=_one_of(LAYOUTS),
            help="blocks (one period at a time) or databook (lines as rows, every period as a "
            "column, subtotals marked).",
        ),
    ] = "blocks",
    unit: Annotated[
        str | None,
        typer.Option(
            "--unit",
            callback=_one_of(tuple(DISPLAY_UNITS)),
            help="rupees, thousands, lakhs, millions or crores. Default: config.toml [display].",
        ),
    ] = None,
    of_revenue: Annotated[
        bool, typer.Option("--of-revenue", help="Databook: each P&L line as a % of revenue.")
    ] = False,
    yoy: Annotated[
        bool, typer.Option("--yoy", help="Databook: growth on the same period a year before.")
    ] = False,
) -> None:
    """Show the profit and loss, balance sheet and cash flow in the standard account order, with
    an "Unmapped / other" line wherever a printed subtotal is not explained by its mapped lines.
    Money is in the display unit (config.toml [display], or --unit), converted in SQL.
    With --rebuilt: each line as reported, as rebuilt from the trial balance, and the difference.
    With --layout databook: every period (or --period) as a column."""
    if rebuilt and scope == "consolidated":
        raise typer.BadParameter(
            "the rebuilt statements are standalone (one company's trial balance)",
            param_hint="--scope",
        )
    if (of_revenue or yoy) and layout != "databook":
        raise typer.BadParameter("needs --layout databook", param_hint="--of-revenue/--yoy")
    if rebuilt and layout == "databook":
        raise typer.BadParameter("--rebuilt has its own layout", param_hint="--layout")

    def body(run: Run) -> None:
        display = display_settings(run.root, company, unit)
        if rebuilt:
            show_rebuilt(run, display)
            return
        rows = statement_rows(run.root, company, period, scope, display)
        if layout == "databook":
            extras = tuple(k for k, on in (("of_revenue", of_revenue), ("yoy", yoy)) if on)
            tables = databook(rows, display, extras)
            text = format_databook(company, tables)
            typer.echo(json.dumps(tables, indent=2, default=str) if json_output else text)
            return
        if period is None:
            latest = latest_annual_period(rows)
            rows = [row for row in rows if row["period"] == latest]
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo(format_statements(company, rows, display))

    def show_rebuilt(run: Run, display: Display) -> None:
        result = rebuilt_rows(run.root, company, period, display)
        if period is None:
            result = select_period(result, latest_annual_period(result["rows"]))
        if json_output:
            shown = {key: value for key, value in result.items() if key != "display"}
            typer.echo(json.dumps(shown, indent=2, default=str))
        else:
            typer.echo(format_rebuilt(company, result))

    params = {
        "company": company, "period": period, "scope": scope, "rebuilt": rebuilt,
        "layout": layout, "unit": unit, "of_revenue": of_revenue, "yoy": yoy,
    }  # fmt: skip
    execute("statements", params, body)


@app.command("export")
def export_command(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
) -> None:
    """Write reports/<company>/<company>_financials.xlsx: the standard statements in the databook
    layout, every period side by side (one sheet per statement and scope), in the display unit
    (config.toml [display]), each figure's source in its cell comment; and a Metrics sheet."""

    show(reports.export_statements(data_dir(), company, via="cli", actor=from_environment()))
