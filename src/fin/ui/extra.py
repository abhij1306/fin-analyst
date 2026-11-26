"""Whether the optional `ui` extra (fastapi, uvicorn) is in this environment. No fin imports, so
`fin.parser` and `fin.update` can use it without a cycle."""

import importlib.util


def extra_installed() -> bool:
    return importlib.util.find_spec("fastapi") is not None


def kept_extras() -> list[str]:
    """`--extra ui` when the UI add-on is installed, so a sync that is about something else (the PDF
    add-on, `fin update`) does not uninstall it."""
    return ["--extra", "ui"] if extra_installed() else []
