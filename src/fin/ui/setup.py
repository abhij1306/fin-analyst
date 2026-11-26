"""`fin ui setup` and the check of whether the UI is installed (docs/web-ui-plan.md).

Installed means the `ui` extra (fastapi, jinja2, uvicorn) is in the environment; the pages, htmx and
Basecoat ship in the repo (static/vendor/), so there is no build step. `setup` runs
`uv sync --frozen --extra ui`, plus the chosen PDF add-on, which a plain sync would remove.
"""

from pathlib import Path

from fin.audit.errors import UpdateFailed
from fin.audit.logger import Run
from fin.config import load_config
from fin.parser import UV_SYNC, _step
from fin.ui.extra import extra_installed

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
TEMPLATE_DIR = HERE / "templates"


def installed() -> bool:
    return extra_installed()


def setup(run: Run) -> list[str]:
    """Install the extra. Returns the lines to print. A failing sync is E005."""
    parser = load_config(run.root).pdf_parser
    extras = ["--extra", "ui", *([] if parser == "none" else ["--extra", parser])]
    return [
        _step(run, "uv sync --extra ui", [*UV_SYNC, *extras], UpdateFailed),
        "The UI is installed. Start it with `uv run fin ui`.",
    ]
