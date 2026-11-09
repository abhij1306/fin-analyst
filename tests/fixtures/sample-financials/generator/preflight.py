"""Find the generator's prerequisites (DejaVu fonts, the recalculation script) before anything is rewritten.

Standard library only. Each finder raises SystemExit with a message when the prerequisite is missing.
"""

import os
from pathlib import Path

FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",  # Linux (Debian, Ubuntu)
    "/usr/share/fonts/dejavu",  # Linux (Fedora)
    "/Library/Fonts",  # macOS
    "~/Library/Fonts",  # macOS (brew install --cask font-dejavu)
)
RECALC_DEFAULT = "/mnt/skills/public/xlsx/scripts/recalc.py"


def find_fonts() -> tuple[Path, Path]:
    """The regular and bold DejaVu Sans files: from FIN_FIXTURE_FONT_DIR if set, else the first known folder
    that has both."""
    override = os.environ.get("FIN_FIXTURE_FONT_DIR")
    candidates = [override] if override else FONT_DIRS
    for folder in candidates:
        paths = tuple(Path(folder).expanduser() / name for name in FONT_FILES)
        if all(p.is_file() for p in paths):
            return paths
    raise SystemExit(
        f"DejaVu fonts ({', '.join(FONT_FILES)}) not found in: {', '.join(map(str, candidates))}. "
        "Install DejaVu or set FIN_FIXTURE_FONT_DIR to the folder that holds them."
    )


def find_recalc() -> Path:
    """The script that recalculates a workbook and caches its formula values: FIN_FIXTURE_RECALC_SCRIPT if set."""
    path = Path(os.environ.get("FIN_FIXTURE_RECALC_SCRIPT", RECALC_DEFAULT))
    if not path.is_file():
        raise SystemExit(
            f"Recalculation script not found: {path}. Set FIN_FIXTURE_RECALC_SCRIPT to recalc.py "
            "(it needs LibreOffice); the Kestrel workbooks must hold calculated values."
        )
    return path
