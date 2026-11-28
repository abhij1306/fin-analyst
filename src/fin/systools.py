"""Where fin finds a system tool (docs/product.md §10).

`install.sh` puts the pinned Quarto in the home folder on macOS (`~/.local/opt`, linked from
`~/.local/bin`), with no sudo. That folder is not always on PATH, so fin looks there first, as the
install does, then on PATH.
"""

import os
import shutil
from pathlib import Path


def user_bin() -> Path:
    return Path.home() / ".local" / "bin"


def which(tool: str) -> str | None:
    """The path of `tool`: `~/.local/bin/<tool>` when it is there and runs, else PATH, else None."""
    candidate = user_bin() / tool
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(tool)
