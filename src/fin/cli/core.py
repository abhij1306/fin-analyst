"""The `fin` app: the root Typer, the `--version` option, the groups from other packages, and the
helpers every command module shares. To add a command group, see docs/development.md."""

from typing import Annotated

import typer

from fin import __version__
from fin.audit.logger import Run
from fin.config import SCOPES
from fin.diligence.adjust_cli import adjust_app
from fin.diligence.cli import engagement_app, gate_app, query_app
from fin.map.commands import map_app, review_app
from fin.metrics.export import refresh_analytics
from fin.parser import parser_app
from fin.paths import COMPANY_NAME

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local financial health analysis.",
)
app.add_typer(map_app, name="map")
app.add_typer(review_app, name="review")
app.add_typer(engagement_app, name="engagement")
app.add_typer(query_app, name="query")
app.add_typer(adjust_app, name="adjust")
app.add_typer(gate_app, name="gate")
app.add_typer(parser_app, name="parser")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fin {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local financial health analysis."""


def check_company(value: str) -> str:
    if not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


def check_optional_company(value: str | None) -> str | None:
    return None if value is None else check_company(value)


def check_scope(value: str | None) -> str | None:
    if value is not None and value not in SCOPES:
        raise typer.BadParameter(f"use one of: {', '.join(SCOPES)}")
    return value


def echo_refresh(run: Run) -> None:
    refresh = refresh_analytics(run)
    typer.echo(
        f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
        f"{refresh.metric_rows} metric rows"
    )
