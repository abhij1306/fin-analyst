"""`fin ui` and `fin ui setup`: the optional web UI (docs/web-ui-plan.md). Without the `ui` extra,
`fin ui` says how to install it (E006); nothing else in fin changes."""

from typing import Annotated

import typer

from fin.audit.errors import UiUnavailable
from fin.audit.logger import Run, execute
from fin.cli.core import app
from fin.ui import setup as ui_setup

ui_app = typer.Typer(
    help="The optional web UI: start it, or install it (`fin ui setup`).",
    invoke_without_command=True,
)
app.add_typer(ui_app, name="ui")


@ui_app.callback()
def ui_command(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1.")] = 8765,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the browser.")] = False,
) -> None:
    """Start the web UI on 127.0.0.1 and open it in the browser; Ctrl-C stops it."""
    if ctx.invoked_subcommand is not None:
        return

    def body(run: Run) -> None:
        if not ui_setup.installed():
            raise UiUnavailable(
                "the web UI is not installed: run `uv run fin ui setup` (or ./install.sh --ui)"
            )
        from fin.ui.server import serve  # needs the ui extra, checked above

        def announce(url: str) -> None:
            typer.echo(f"fin UI: {url}\nlocal only; press Ctrl-C to stop")
            run.event("ui", f"started on 127.0.0.1:{port}")  # the link's token is not logged

        serve(run.root, port, not no_open, announce)

    execute("ui", {"port": port, "no_open": no_open}, body)


@ui_app.command("setup")
def setup_command() -> None:
    """Install the UI add-on (`uv sync --extra ui`)."""

    def body(run: Run) -> None:
        typer.echo("\n".join(ui_setup.setup(run)))

    execute("ui setup", {}, body)
