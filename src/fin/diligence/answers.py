"""`fin query answer <co> <id> --file reply`: record a reply file with the answer.

The file is archived like any source (`archive/<company>/<sha256>.<ext>`) and named in the query's
`answer_source`. A plain-text reply (.txt, .eml, .md) is read and, as written, is the answer
unless `--text` gives one; any other file (Excel, Word,
PDF) needs `--text` to say what it answers, since fin does not read it. E860 otherwise.

The archive copy is made inside the transaction that moves the query to answered
(`fin.services.diligence.answer_query`), so a refused answer leaves no file.
"""

from dataclasses import dataclass
from pathlib import Path

from fin.audit.errors import ReplyFileError
from fin.audit.logger import Run
from fin.diligence.sources import archive_copy
from fin.ingest import sha256_of

TEXT_SUFFIXES = (".txt", ".eml", ".md")


@dataclass(frozen=True)
class Reply:
    """A reply file read and checked, not archived yet: the answer and where it will be."""

    path: Path
    sha256: str
    text: str
    source: str


def read_reply(company: str, path: Path, text: str | None) -> Reply:
    """The answer and answer_source of a reply file, without archiving it. E860."""
    if not path.is_file():
        raise ReplyFileError(f"reply file {path} does not exist; nothing recorded")
    plain = path.suffix.lower() in TEXT_SUFFIXES
    if text is None and not plain:
        raise ReplyFileError(
            f"{path.name} is not plain text (.txt, .eml, .md): fin does not read it. Give "
            "--text with what it answers; nothing recorded"
        )
    try:
        content = path.read_text(encoding="utf-8") if text is None else ""
        sha256 = sha256_of(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise ReplyFileError(f"cannot read {path}: {exc}; nothing recorded") from exc
    if text is None:
        text = content.strip()
        if not text:
            raise ReplyFileError(f"{path.name} is empty; nothing recorded")
    archived = f"{sha256}{path.suffix.lower()}"  # the name archive_copy gives it
    return Reply(path, sha256, text, f"{path.name} (archive/{company}/{archived})")


def archive_reply(run: Run, company: str, reply: Reply) -> None:
    """Copy the reply file into the archive (E004 when the data folder is not writable)."""
    archive_copy(run, company, reply.path, reply.sha256)
