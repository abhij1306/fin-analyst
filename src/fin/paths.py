"""Data folder resolution. The data folder lives outside the repo (docs/product.md §6)."""

import os
import re
import uuid
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable, PathOutsideFolder

# A company name: also the name of its folders, so it can never point outside them.
COMPANY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DATA_DIR_ENV = "FIN_DATA_DIR"
DEFAULT_DATA_DIR_NAME = "FinAnalysis"

# Top-level folders of the data folder (product.md §6). Per-company folders are created on ingest.
DATA_SUBDIRS = (
    "inbox",
    "archive",
    "companies",
    "shared",
    "review",
    "notes",
    "evidence",
    "reports",
    "logs",
    "backups",
)


def data_dir() -> Path:
    """Return the data folder: `$FIN_DATA_DIR` if set, else `~/FinAnalysis`."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / DEFAULT_DATA_DIR_NAME).resolve()


def logs_dir() -> Path:
    return data_dir() / "logs"


def config_path(root: Path) -> Path:
    return root / "config.toml"


def audit_db_path(root: Path) -> Path:
    return root / "shared" / "audit.duckdb"


def dictionary_db_path(root: Path) -> Path:
    return root / "shared" / "dictionary.duckdb"


def analytics_db_path(root: Path) -> Path:
    return root / "shared" / "analytics.duckdb"


def companies_dir(root: Path) -> Path:
    return root / "companies"


def company_db_path(root: Path, company: str) -> Path:
    return companies_dir(root) / f"{company}.duckdb"


def inbox_dir(root: Path, company: str) -> Path:
    return root / "inbox" / company


def archive_dir(root: Path, company: str) -> Path:
    return root / "archive" / company


def review_dir(root: Path, company: str) -> Path:
    return root / "review" / company


def notes_dir(root: Path, company: str) -> Path:
    return root / "notes" / company


def reports_dir(root: Path, company: str) -> Path:
    return root / "reports" / company


def dataroom_dir(root: Path, company: str) -> Path:
    """Where `fin dataroom scan` unpacks a company's archives, one folder each. Made on use."""
    return root / "dataroom" / company


def exports_dir(root: Path, company: str) -> Path:
    return root / "exports" / company


def company_evals_dir(root: Path, company: str) -> Path:
    return evals_dir(root) / company


def backups_dir(root: Path) -> Path:
    return root / "backups"


def evals_dir(root: Path) -> Path:
    """Golden sets, eval reports and results bundles (docs/client-handoff.md). Made on first use."""
    return root / "evals"


def probe_writable(folder: Path) -> None:
    """Raise E003 unless a file can be created and removed in `folder`."""
    probe = folder / ".fin_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write to {folder}: {exc}") from exc


def ensure_data_folder(root: Path) -> tuple[list[Path], list[Path]]:
    """Create the data folder tree if missing. Returns (created, existing). Never deletes."""
    created: list[Path] = []
    existing: list[Path] = []
    for folder in (root, *(root / name for name in DATA_SUBDIRS)):
        if folder.is_dir():
            existing.append(folder)
            continue
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot create {folder}: {exc}") from exc
        created.append(folder)
    probe_writable(root)
    return created, existing


def inside(root: Path, path: Path) -> Path:
    """`path` resolved (links followed), or E013 when it is not inside `root` (also resolved).
    Every path from a file, a request or a database row is checked with this before it is read or
    written (docs/engineering.md rule 8)."""
    base, resolved = root.resolve(), path.resolve()
    if not resolved.is_relative_to(base):
        raise PathOutsideFolder(f"{path} is outside {root}").with_context(
            root=str(base), path=str(resolved)
        )
    return resolved


def temp_name(path: Path) -> Path:
    """A unique hidden sibling of `path` for a write that is replaced into place: two runs never
    share it (docs/engineering.md rule 9)."""
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write `data` (text as UTF-8) to a unique temporary sibling, then replace `path` with it in
    one step, so a reader sees the old file or the new one, never part of it. E003 when it cannot
    be written; the temporary file is removed."""
    temp = temp_name(path)
    body = data.encode("utf-8") if isinstance(data, str) else data
    try:
        with temp.open("xb") as handle:
            handle.write(body)
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc
