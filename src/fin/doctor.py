"""`fin doctor`: check dependencies, versions and the data folder (PLAN.md §10)."""

import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from fin.audit.errors import (
    DataFolderNotWritable,
    DependencyMissing,
    DependencyVersionMismatch,
)
from fin.paths import probe_writable

PYTHON_VERSION = (3, 12)

# Homebrew cannot pin exact versions, so system tools have minimums.
# Python dependencies are pinned exactly in uv.lock.
TOOL_MINIMUMS: dict[str, tuple[int, ...]] = {
    "uv": (0, 11),
    "node": (24,),
    "quarto": (1, 7),
}

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    code: str | None = None
    fix: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def tool_version(tool: str) -> str | None:
    """Return the `--version` output of `tool`, or None if it is not on PATH."""
    exe = shutil.which(tool)
    if exe is None:
        return None
    result = subprocess.run(
        [exe, "--version"], capture_output=True, text=True, check=False, timeout=60
    )
    return (result.stdout or result.stderr).strip()


def parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _fmt(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def check_python() -> Check:
    found = sys.version_info[:3]
    if found[:2] == PYTHON_VERSION:
        return Check("python", True, _fmt(found))
    return Check(
        "python",
        False,
        f"found {_fmt(found)}, need {_fmt(PYTHON_VERSION)}.x",
        DependencyVersionMismatch.code,
        "run `uv sync --frozen` from the repo (it uses .python-version)",
    )


def check_tool(tool: str, minimum: tuple[int, ...]) -> Check:
    output = tool_version(tool)
    if output is None:
        return Check(
            tool,
            False,
            "not found on PATH",
            DependencyMissing.code,
            "run ./install.sh (installs it with `brew bundle --file=Brewfile`)",
        )
    found = parse_version(output)
    if found is None:
        return Check(
            tool,
            False,
            f"cannot read version from {output!r}",
            DependencyVersionMismatch.code,
            f"reinstall with `brew reinstall {tool}`",
        )
    if found < minimum:
        return Check(
            tool,
            False,
            f"found {_fmt(found)}, need >= {_fmt(minimum)}",
            DependencyVersionMismatch.code,
            f"run `brew upgrade {tool}`",
        )
    return Check(tool, True, _fmt(found))


def check_data_folder(root: Path) -> Check:
    if not root.is_dir():
        return Check(
            "data folder",
            False,
            f"{root} does not exist",
            DataFolderNotWritable.code,
            "run `uv run fin init`",
        )
    try:
        probe_writable(root)
    except DataFolderNotWritable as exc:
        return Check(
            "data folder",
            False,
            exc.message,
            exc.code,
            f"fix permissions on {root} or set FIN_DATA_DIR",
        )
    return Check("data folder", True, str(root))


def run_checks(root: Path) -> list[Check]:
    checks = [check_python()]
    checks.extend(check_tool(tool, minimum) for tool, minimum in TOOL_MINIMUMS.items())
    checks.append(check_data_folder(root))
    return checks
