"""Data folder resolution. The data folder lives outside the repo (PLAN.md §6)."""

import os
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable

DATA_DIR_ENV = "FIN_DATA_DIR"
DEFAULT_DATA_DIR_NAME = "FinAnalysis"

# Top-level folders of the data folder (PLAN.md §6). Per-company folders are created on ingest.
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


def backups_dir(root: Path) -> Path:
    return root / "backups"


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
