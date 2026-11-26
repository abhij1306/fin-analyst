"""`fin query answer <co> <id> --file reply`: record a reply file with the answer.

The file is archived like any source (`archive/<company>/<sha256>.<ext>`) and named in the query's
`answer_source`. A plain-text reply (.txt, .eml, .md) is read and, as written, is the answer
unless `--text` gives one; any other file (Excel, Word,
PDF) needs `--text` to say what it answers, since fin does not read it. E860 otherwise.
"""

from pathlib import Path

from fin.audit.errors import ReplyFileError
from fin.audit.logger import Run
from fin.diligence.sources import archive_copy
from fin.ingest import sha256_of

TEXT_SUFFIXES = (".txt", ".eml", ".md")


def record_reply(run: Run, company: str, path: Path, text: str | None) -> tuple[str, str]:
    """(answer, answer_source) for a reply file: the file is archived first. E860."""
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
        archived = archive_copy(run, company, path, sha256_of(path))
    except (OSError, UnicodeDecodeError) as exc:
        raise ReplyFileError(f"cannot read or archive {path}: {exc}; nothing recorded") from exc
    if text is None:
        text = content.strip()
        if not text:
            raise ReplyFileError(f"{path.name} is empty; nothing recorded")
    return text, f"{path.name} (archive/{company}/{Path(archived).name})"
