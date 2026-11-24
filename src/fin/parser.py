"""`fin parser show|use <name>`: choose the PDF add-on, the one engine that reads the statement
pages pdfplumber skips (`fin.extract.addon`): none, Docling, LiteParse or LlamaParse.

`use <name>` runs these steps, each logged; a failing step stops it with its code:

1. Refuses an engine this Mac cannot run (Docling on an Intel Mac: E001).
2. `uv sync --frozen --extra <name>` (`none`: a plain `uv sync --frozen`, which removes every
   add-on). A failing sync is E005.
3. Downloads the engine's models when it needs them and they are missing (Docling about 700 MB,
   LiteParse's ocria-nano about 50 MB), in a new process so it uses the packages just synced. A
   failing download is E206.
4. Writes `[pdf] parser = "<name>"` in config.toml (`fin.config.set_parser`).

It prints what changed. For LlamaParse it says that pages are uploaded to LlamaParse's cloud and
whether LLAMA_CLOUD_API_KEY is set, never its value. `fin update` keeps the chosen extra.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import DependencyMissing, PdfAddonFailed, UpdateFailed
from fin.audit.logger import Run, execute
from fin.config import PARSERS, load_config, set_parser
from fin.extract import llamaparse_run
from fin.extract.fallback import engine

REPO = Path(__file__).resolve().parents[2]
# Module constants so tests can swap the slow steps for harmless ones.
UV_SYNC = ["uv", "sync", "--frozen"]
PYTHON = [sys.executable, "-I", "-c"]
_DOWNLOAD = (
    "from fin.extract.fallback import ENGINES\n"
    "e = ENGINES[{name!r}]\n"
    "if e.ready()[1] is not None: e.download_models()\n"
)
ABOUT = {
    "none": "no PDF add-on: pages pdfplumber skips are listed as not read",
    "docling": "Docling: local; about 1.4 GB; about 15 s a page on CPU",
    "liteparse": "LiteParse: local; small and fast",
    "llamaparse": "LlamaParse: cloud; uploads the pages; paid per page; needs LLAMA_CLOUD_API_KEY",
}

parser_app = typer.Typer(
    no_args_is_help=True,
    help="The PDF add-on that reads the pages pdfplumber skips: show it, or choose one.",
)


def describe(root: Path) -> list[str]:
    """The chosen add-on, whether it is installed, its models or key, and the choices."""
    name = load_config(root).pdf_parser
    lines = [f'[pdf] parser = "{name}" ({ABOUT[name]})']
    chosen = engine(name)
    if chosen is not None:
        if not chosen.installed():
            lines.append(f"  not installed: run `uv run fin parser use {name}`")
        else:
            detail, fix = chosen.ready()
            lines.append(f"  installed; {detail}" + (f" ({fix})" if fix else ""))
    lines.append("choices: " + ", ".join(PARSERS))
    return lines


def use(run: Run, name: str) -> list[str]:
    """Install `name` (or remove every add-on for "none"), download its models, record it. Returns
    the lines to print."""
    before = load_config(run.root).pdf_parser
    chosen = engine(name)
    if chosen is not None and (reason := chosen.unsupported()):
        raise DependencyMissing(f"{reason}; choose another add-on (`fin parser use liteparse`)")
    sync = [*UV_SYNC, "--extra", name] if chosen is not None else UV_SYNC
    step = f"uv sync --extra {name}" if chosen is not None else "uv sync"
    lines = [_step(run, step, sync, UpdateFailed)]
    if chosen is not None and chosen.NAME != llamaparse_run.NAME:
        script = _DOWNLOAD.format(name=name)
        lines.append(_step(run, f"{chosen.LABEL} models", [*PYTHON, script], PdfAddonFailed))
    set_parser(run.root, name)
    change = f'[pdf] parser = "{name}" in config.toml (was "{before}")'
    run.event("parser", f"set {change}")
    lines.append(f"set      {change}")
    lines.append(ABOUT[name])
    if name == llamaparse_run.NAME:
        state = (
            "is set" if llamaparse_run.key_set() else "is NOT set: pages stay unread until it is"
        )
        lines.append(
            "LlamaParse uploads the pages pdfplumber skips (only those) to LlamaParse's cloud. "
            f"{llamaparse_run.API_KEY_ENV} {state} (fin reads it only from the environment)."
        )
    return lines


def _step(
    run: Run, step: str, command: list[str], error: type[UpdateFailed | PdfAddonFailed]
) -> str:
    """Run one command in the repo and log it. Raises `error` if it cannot start or fails."""
    program = shutil.which(command[0])
    if program is None:
        raise error(f"{step}: {command[0]} not found; run `uv run fin doctor`")
    try:
        result = subprocess.run(
            [program, *command[1:]], cwd=REPO, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise error(f"{step}: cannot start {command[0]}: {exc}") from exc
    output = (result.stdout + result.stderr).strip()[-500:]
    if result.returncode != 0:
        raise error(f"{step} failed (exit {result.returncode}): {output}")
    run.event("parser", f"{step}: exit 0", context={"output": output})
    return f"done     {step}"


def _parser_name(value: str) -> str:
    if value not in PARSERS:
        raise typer.BadParameter(f"use one of: {', '.join(PARSERS)}")
    return value


@parser_app.command("show")
def show_command() -> None:
    """Show the chosen PDF add-on, whether it is installed, and its models or key."""

    def body(run: Run) -> None:
        lines = describe(run.root)
        typer.echo("\n".join(lines))
        run.event("parser", lines[0])

    execute("parser show", {}, body)


@parser_app.command("use")
def use_command(
    name: Annotated[
        str,
        typer.Argument(
            callback=_parser_name,
            help="none, docling, liteparse or llamaparse.",
            show_default=False,
        ),
    ],
) -> None:
    """Choose the PDF add-on: install it (uv sync --extra), download its models, record it in
    config.toml. `none` removes every add-on."""

    def body(run: Run) -> None:
        typer.echo("\n".join(use(run, name)))

    execute("parser use", {"name": name}, body)
