"""`fin doctor`: check dependencies, versions, the data folder, config.toml and the audit schema
(PLAN.md §10)."""

import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from fin.audit.errors import (
    DatabaseLocked,
    DataFolderNotWritable,
    DependencyMissing,
    DependencyVersionMismatch,
    FinError,
    SchemaNewerThanCode,
)
from fin.config import load_config
from fin.db.migrate import current_version, latest_version
from fin.paths import audit_db_path, probe_writable

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


def check_config(root: Path) -> Check:
    try:
        config = load_config(root)
    except DependencyMissing as exc:
        return Check("config", False, exc.message, exc.code, "run `uv run fin init`")
    except FinError as exc:
        return Check(
            "config",
            False,
            exc.message,
            exc.code,
            "correct config.toml (or rename it and run `uv run fin init` for a fresh default)",
        )
    return Check("config", True, f"display unit {config.display_unit}")


def check_audit_schema(root: Path) -> Check:
    path = audit_db_path(root)
    latest = latest_version("audit")
    if not path.is_file():
        return Check(
            "audit schema",
            False,
            f"{path} does not exist",
            DependencyMissing.code,
            "run `uv run fin init`",
        )
    try:
        version = current_version(path)
    except DatabaseLocked as exc:
        return Check("audit schema", False, exc.message, exc.code, "close other fin commands")
    if version < latest:
        return Check(
            "audit schema",
            False,
            f"version {version}, expected {latest}",
            DependencyVersionMismatch.code,
            "run `uv run fin migrate`",
        )
    if version > latest:
        return Check(
            "audit schema",
            False,
            f"version {version} is newer than this fin ({latest})",
            SchemaNewerThanCode.code,
            "update fin: `git pull`, then `uv sync --frozen`",
        )
    return Check("audit schema", True, f"version {version}")


def run_checks(root: Path) -> list[Check]:
    checks = [check_python()]
    checks.extend(check_tool(tool, minimum) for tool, minimum in TOOL_MINIMUMS.items())
    checks.append(check_data_folder(root))
    checks.append(check_config(root))
    checks.append(check_audit_schema(root))
    return checks
