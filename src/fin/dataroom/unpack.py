"""Unpack a data room's .zip archive into its own new folder (SPEC-13), or refuse it.

Every entry is checked before anything is written. The archive is refused, and nothing is unpacked,
when an entry's path is absolute, names a drive or climbs out with "..", an entry is a link, an
entry is password protected, or the entries add up to more than the budget (`[dataroom]
archive_limit_mb`, shared by an archive and the archives inside it, so an archive of archives cannot
multiply it). Entries are written into a staging folder with a unique name, each checked again after
resolving the path against the folder, and counted as they are written (a size in the archive's
index can lie); the staging folder becomes the archive's folder in one rename, so a folder that
exists was unpacked whole by an earlier scan and is used as it is.
"""

import re
import shutil
import stat
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable

CHUNK = 1 << 20
MAX_ENTRIES = 100_000  # entries one archive and the archives inside it may hold
_SEPARATORS = re.compile(r"[\\/]")  # a Windows-made archive may use backslashes


@dataclass
class Budget:
    """What one archive and every archive inside it may still unpack to: an archive of archives
    cannot multiply the limit."""

    bytes: int
    entries: int = MAX_ENTRIES


class _Refused(Exception):
    """An entry found while writing makes the archive refused: `reason` is a key of REASONS."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def unpack(archive: Path, target: Path, budget: Budget) -> str | None:
    """Unpack `archive` into `target` within `budget`, taking what it used from it. None when
    unpacked (or unpacked by an earlier scan); else the key of REASONS it was refused for, with
    nothing left behind. E003 when the data folder cannot be written."""
    if target.is_dir():
        return None
    try:
        with zipfile.ZipFile(archive) as book:
            infos = book.infolist()
            reason = _check(infos, budget)
            if reason is None:
                reason = _extract_all(book, infos, target, budget)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, zlib.error, NotImplementedError):
        # damaged data, or a compression method zipfile cannot read
        return "archive_unreadable"
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot unpack {archive.name} into {target}: {exc}") from exc
    return reason


def _check(infos: list[zipfile.ZipInfo], budget: Budget) -> str | None:
    """The reason to refuse the archive from its index alone, or None."""
    for info in infos:
        if info.flag_bits & 0x1:
            return "archive_unreadable"
        if stat.S_ISLNK(info.external_attr >> 16) or not _safe_name(info.filename):
            return "archive_unsafe"
    if len(infos) > budget.entries or sum(info.file_size for info in infos) > budget.bytes:
        return "archive_too_large"
    return None


def _safe_name(name: str) -> bool:
    """A relative path inside the folder: not absolute, no drive, no ".." part."""
    parts = _SEPARATORS.split(name)
    return bool(name) and parts[0] != "" and ":" not in parts[0] and ".." not in parts


def _extract_all(
    book: zipfile.ZipFile, infos: list[zipfile.ZipInfo], target: Path, budget: Budget
) -> str | None:
    staging = target.with_name(f".{target.name}-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    written = 0
    try:
        for info in infos:
            written += _extract(book, info, staging, budget.bytes - written)
        staging.rename(target)
    except _Refused as refused:
        return refused.reason
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    budget.bytes -= written
    budget.entries -= len(infos)
    return None


def _extract(book: zipfile.ZipFile, info: zipfile.ZipInfo, staging: Path, budget: int) -> int:
    """Write one entry under `staging`; the bytes written."""
    root = staging.resolve()
    dest = (staging / Path(*_SEPARATORS.split(info.filename.rstrip("/\\")))).resolve()
    if not dest.is_relative_to(root) or dest == root:
        raise _Refused("archive_unsafe")
    written = 0
    try:
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            return 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():  # opening a folder fails differently on each system: refuse it first
            raise FileExistsError(dest)
        with book.open(info) as source, dest.open("xb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                if written > budget:
                    raise _Refused("archive_too_large")
                out.write(chunk)
    except (FileExistsError, NotADirectoryError) as exc:
        # two entries with one path, or a file entry where another entry needs a folder
        raise _Refused("archive_unsafe") from exc
    return written
