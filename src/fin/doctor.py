"""`fin doctor`: check dependencies, versions, the data folder, config.toml and the shared
database schemas (PLAN.md §10)."""

import importlib
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
)
from fin.config import load_config
from fin.db.conn import connect
from fin.db.schema import is_old_format
from fin.extract import docling_run
from fin.paths import audit_db_path, dictionary_db_path, probe_writable

PYTHON_VERSION = (3, 12)
DATA_FOLDER = "data folder"  # the name of that check
RUN_INIT = "run `uv run fin init`"  # the fix when the data folder, config or a schema is missing

# Homebrew cannot pin exact versions, so system tools have minimums.
# Python dependencies are pinned exactly in uv.lock.
TOOL_MINIMUMS: dict[str, tuple[int, ...]] = {
    "uv": (0, 11),
    "node": (24,),
    "quarto": (1, 7),
}

_DIGITS_AND_DOTS = re.compile(r"[\d.]+")  # where a version such as "1.2" or "1.2.3" can be


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    code: str | None = None
    fix: str | None = None
    warning: bool = False  # ok (not a failure), but something is worth fixing

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
    """The first "major.minor" or "major.minor.patch" in `text`, as numbers."""
    for run in _DIGITS_AND_DOTS.finditer(text):
        parts = run.group().split(".")
        for i in range(len(parts) - 1):
            if parts[i] and parts[i + 1]:
                version = parts[i : i + 2]
                if i + 2 < len(parts) and parts[i + 2]:
                    version.append(parts[i + 2])
                return tuple(int(part) for part in version)
    return None


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
    try:
        output = tool_version(tool)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Check(
            tool,
            False,
            f"`{tool} --version` did not run ({exc})",
            DependencyMissing.code,
            f"reinstall with `brew reinstall {tool}`",
        )
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


def check_poppler() -> Check:
    """poppler's pdftoppm lets Claude Code read PDF pages as images for readings (`fin ingest
    --reading`). fin itself does not need it, so a missing poppler is a warning."""
    try:
        output = tool_version("pdftoppm")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Check(
            "poppler",
            True,
            f"`pdftoppm --version` did not run ({exc}): Claude cannot read PDF pages as images",
            fix="run `brew reinstall poppler`",
            warning=True,
        )
    if output is None:
        return Check(
            "poppler",
            True,
            "pdftoppm not found on PATH: Claude cannot read PDF pages as images for readings",
            fix="run ./install.sh (installs it with `brew bundle --file=Brewfile`)",
            warning=True,
        )
    found = parse_version(output)
    return Check("poppler", True, _fmt(found) if found else "found")


def check_docling(root: Path) -> Check:
    """The optional Docling PDF add-on: off is fine; on, it must import, and its models should be
    downloaded (ingest downloads them at first use otherwise, so missing models are a warning)."""
    try:
        on = load_config(root).docling
    except FinError:
        on = False  # the config check reports it
    if not on:
        detail = "installed, off in config.toml" if docling_run.installed() else "not installed"
        return Check("docling", True, f"{detail} (optional; `./install.sh --docling` to add it)")
    try:
        importlib.import_module("docling.document_converter")
    except ImportError as exc:
        return Check(
            "docling",
            False,
            f"[pdf] docling = true in config.toml, but Docling does not import ({exc})",
            DependencyMissing.code,
            "run `./install.sh --docling` (or `./install.sh --no-docling` to turn it off)",
        )
    if not docling_run.models_present():
        return Check(
            "docling",
            True,
            f"installed, but its models are not in {docling_run.models_dir()} (ingest downloads "
            "them at first use)",
            fix=f"run `{docling_run.MODEL_DOWNLOAD}`",
            warning=True,
        )
    return Check("docling", True, f"installed, models in {docling_run.models_dir()}")


def check_data_folder(root: Path) -> Check:
    if not root.is_dir():
        return Check(
            DATA_FOLDER,
            False,
            f"{root} does not exist",
            DataFolderNotWritable.code,
            RUN_INIT,
        )
    try:
        probe_writable(root)
    except DataFolderNotWritable as exc:
        return Check(
            DATA_FOLDER,
            False,
            exc.message,
            exc.code,
            f"fix permissions on {root} or set FIN_DATA_DIR",
        )
    return Check(DATA_FOLDER, True, str(root))


def check_config(root: Path) -> Check:
    try:
        config = load_config(root)
    except DependencyMissing as exc:
        return Check("config", False, exc.message, exc.code, RUN_INIT)
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
    return check_schema(audit_db_path(root), "audit")


def check_schema(path: Path, kind: str) -> Check:
    """A shared database exists and was made by this fin's layout (not an older one)."""
    name = f"{kind} schema"
    if not path.is_file():
        return Check(
            name,
            False,
            f"{path} does not exist",
            DependencyMissing.code,
            RUN_INIT,
        )
    try:
        with connect(path) as con:
            old = is_old_format(con)
    except DatabaseLocked as exc:
        return Check(name, False, exc.message, exc.code, "close other fin commands")
    if old:
        return Check(
            name,
            False,
            "made by an older fin",
            DependencyVersionMismatch.code,
            "move the data folder away and re-create it with `uv run fin init`",
        )
    return Check(name, True, "present")


def run_checks(root: Path) -> list[Check]:
    tools = [check_tool(tool, minimum) for tool, minimum in TOOL_MINIMUMS.items()]
    return [
        check_python(),
        *tools,
        check_poppler(),
        check_data_folder(root),
        check_config(root),
        check_audit_schema(root),
        check_schema(dictionary_db_path(root), "dictionary"),
        check_docling(root),
    ]
