"""Files the user adds through the UI (`fin.ui`): they go into the company's inbox folder."""

from pathlib import Path
from typing import BinaryIO

from fin.audit.logger import Run
from fin.paths import inbox_dir
from fin.services.result import ActionResult, perform
from fin.ui.uploads import save_uploads


def upload(root: Path, company: str, files: list[tuple[str, BinaryIO]]) -> ActionResult:
    """Put files into the company's inbox folder. Ingest is not run: Claude ingests them in chat."""
    inbox = inbox_dir(root, company)

    def body(run: Run) -> list[str]:
        saved = save_uploads(inbox, files)
        for name in saved:
            run.event("ui upload", f"saved {name} to inbox/{company}/", file=f"{company}/{name}")
        return [f"{name} is waiting in inbox/{company}/" for name in saved] + [
            "Nothing is loaded yet: ask Claude to ingest the files."
        ]

    return perform(root, "ui upload", {"company": company, "files": [n for n, _ in files]}, body)
