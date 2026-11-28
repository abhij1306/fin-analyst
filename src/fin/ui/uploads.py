"""Saving uploaded files into a company's inbox folder, safely (docs/web-ui-plan.md).

A plain file name (no folders, no dot first, letters, digits, space and . _ - ( ) &), a supported
format, a size limit, never over an existing file, and only inside the inbox folder. All files are
checked before any is written. Every refusal is E008."""

import re
import shutil
from pathlib import Path
from typing import BinaryIO

from fin.audit.errors import UiActionInvalid

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_REQUEST_BYTES = 100 * 1024 * 1024
SUFFIXES = (".xlsx", ".xlsm", ".xls", ".pdf")  # what `fin ingest` reads
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()&-]{0,150}")


def safe_name(raw: str, suffixes: tuple[str, ...] = SUFFIXES) -> str:
    """The file name, or E008. A path is cut to its last part (browsers send the bare name; a
    client that sends a path gets no folder)."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not SAFE_NAME.fullmatch(name) or ".." in name:
        raise UiActionInvalid(f"{raw!r} is not a plain file name; rename it and upload again")
    if not name.lower().endswith(suffixes):
        raise UiActionInvalid(f"{name}: fin reads {', '.join(suffixes)} files only")
    return name


def check_length(header: str | None) -> None:
    """E008 for a request whose Content-Length is missing, not a number or over
    MAX_REQUEST_BYTES, before its body is read."""
    size = int(header) if header and header.isascii() and header.isdigit() else -1
    if size not in range(MAX_REQUEST_BYTES + 1):
        raise UiActionInvalid("the upload is empty, of unknown size or too large")


def _check_size(handle: BinaryIO, name: str) -> None:
    """E008 when the uploaded file is over MAX_FILE_BYTES; the handle is left at its start."""
    handle.seek(0, 2)
    if handle.tell() > MAX_FILE_BYTES:
        raise UiActionInvalid(f"{name} is over {MAX_FILE_BYTES // 1024 // 1024} MB")
    handle.seek(0)


def _write(handle: BinaryIO, target: Path) -> None:
    with target.open("xb") as out:
        shutil.copyfileobj(handle, out)


def save_one(handle: BinaryIO, target: Path) -> None:
    """Write one uploaded file to `target` (a new file). E008 over MAX_FILE_BYTES."""
    _check_size(handle, target.name)
    _write(handle, target)


def save_uploads(
    inbox: Path, files: list[tuple[str, BinaryIO]], suffixes: tuple[str, ...] = SUFFIXES
) -> list[str]:
    """Write the files into `inbox` (a data-room upload: its own folder, `.zip` only); returns
    their names. E008 refuses all and writes none."""
    if not files:
        raise UiActionInvalid("choose at least one file")
    names = [safe_name(raw, suffixes) for raw, _ in files]
    folder = inbox.resolve()
    targets = [(folder / name) for name in names]
    if len({name.lower() for name in names}) != len(names):
        raise UiActionInvalid("two of the files have the same name")
    for target in targets:
        if target.parent != folder or target.exists():
            raise UiActionInvalid(
                f"{target.name} is in the inbox already (or not a plain name); rename the file"
            )
    pairs = [(handle, target) for (_, handle), target in zip(files, targets, strict=True)]
    for handle, target in pairs:
        _check_size(handle, target.name)
    folder.mkdir(parents=True, exist_ok=True)
    for handle, target in pairs:
        _write(handle, target)
    return names
