"""The fix hint for a missing or outdated system tool, for the OS fin runs on (docs/product.md §10).

macOS installs system tools with Homebrew, except Quarto, which `./install.sh` installs from its
pinned release into the home folder (no sudo); Linux (Debian/Ubuntu) with apt and pinned downloads.
In both, `./install.sh` installs them; macOS also names the brew command.
"""

import sys

QUARTO_MACOS = "run ./install.sh (installs the pinned Quarto into your home folder, no sudo)"


def is_macos() -> bool:
    return sys.platform == "darwin"


def missing(tool: str) -> str:
    """The fix when a tool is not installed."""
    if is_macos() and tool == "quarto":
        return QUARTO_MACOS
    if is_macos():
        return "run ./install.sh (installs it with `brew bundle --file=Brewfile`)"
    return "run ./install.sh (installs it with apt-get and pinned downloads)"


def reinstall(tool: str) -> str:
    """The fix when a tool does not run or its version cannot be read."""
    if is_macos() and tool == "quarto":
        return "remove ~/.local/bin/quarto and " + QUARTO_MACOS
    if is_macos():
        return f"reinstall with `brew reinstall {tool}`"
    return "run ./install.sh to install it again (remove the broken copy first)"


def upgrade(tool: str) -> str:
    """The fix when a tool is older than the minimum."""
    if is_macos() and tool == "quarto":
        return QUARTO_MACOS
    if is_macos():
        return f"run `brew upgrade {tool}`"
    return "run ./install.sh (installs a current version)"


def quarto_missing() -> str:
    """The message when `fin report --render` finds no quarto."""
    if is_macos():
        return "quarto is not installed; " + QUARTO_MACOS
    return "quarto is not on PATH; run ./install.sh (installs it from the Quarto .deb release)"


def poppler_reinstall() -> str:
    if is_macos():
        return "run `brew reinstall poppler`"
    return "run `apt-get install --reinstall poppler-utils` (with sudo unless you are root)"
