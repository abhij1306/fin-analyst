"""Unpack a data room's .zip archive into its own new folder (SPEC-13), or refuse it.

Every entry is checked before anything is written. The archive is refused, and nothing is
unpacked, when an entry's path is absolute, names a drive or climbs out with "..", an entry is a
link, an entry is password protected, or the entries add up to more than
`[dataroom] archive_limit_mb`. Entries are written into a staging folder with a unique name, each
checked again after resolving the path against the folder, and counted as they are written (a
size in the archive's index can lie); the staging folder becomes the archive's folder in one
rename, so a folder that exists was unpacked whole by an earlier scan and is used as it is.
"""

import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable

CHUNK = 1 << 20
_SEPARATORS = re.compile(r"[\\/]")  # a Windows-made archive may use backslashes


class _Refused(Exception):
    """An entry found while writing makes the archive refused: `reason` is a key of REASONS."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def unpack(archive: Path, target: Path, limit_bytes: int) -> str | None:
    """Unpack `archive` into `target`. None when unpacked (or unpacked by an earlier scan); else
    the key of REASONS it was refused for, with nothing left behind. E003 when the data folder
    cannot be written."""
    if target.is_dir():
        return None
    try:
        with zipfile.ZipFile(archive) as book:
            infos = book.infolist()
            reason = _check(infos, limit_bytes)
            if reason is None:
                reason = _extract_all(book, infos, target, limit_bytes)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError):
        return "archive_unreadable"
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot unpack {archive.name} into {target}: {exc}") from exc
    return reason


def _check(infos: list[zipfile.ZipInfo], limit_bytes: int) -> str | None:
    """The reason to refuse the archive from its index alone, or None."""
    for info in infos:
        if info.flag_bits & 0x1:
            return "archive_unreadable"
        if stat.S_ISLNK(info.external_attr >> 16) or not _safe_name(info.filename):
            return "archive_unsafe"
    if sum(info.file_size for info in infos) > limit_bytes:
        return "archive_too_large"
    return None


def _safe_name(name: str) -> bool:
    """A relative path inside the folder: not absolute, no drive, no ".." part."""
    parts = _SEPARATORS.split(name)
    return bool(name) and parts[0] != "" and ":" not in parts[0] and ".." not in parts


def _extract_all(
    book: zipfile.ZipFile, infos: list[zipfile.ZipInfo], target: Path, limit_bytes: int
) -> str | None:
    staging = target.with_name(f".{target.name}-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        written = 0
        for info in infos:
            written = _extract(book, info, staging, limit_bytes - written) + written
        staging.rename(target)
    except _Refused as refused:
        return refused.reason
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return None


def _extract(book: zipfile.ZipFile, info: zipfile.ZipInfo, staging: Path, budget: int) -> int:
    """Write one entry under `staging`; the bytes written."""
    root = staging.resolve()
    dest = (staging / Path(*_SEPARATORS.split(info.filename.rstrip("/\\")))).resolve()
    if not dest.is_relative_to(root) or dest == root:
        raise _Refused("archive_unsafe")
    if info.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with book.open(info) as source, dest.open("xb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                if written > budget:
                    raise _Refused("archive_too_large")
                out.write(chunk)
    except FileExistsError as exc:
        raise _Refused("archive_unsafe") from exc
    return written
