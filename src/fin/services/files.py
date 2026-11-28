"""Files and companies in the UI (`fin.ui`, docs/specs/SPEC-03): upload into the company's inbox
folder, create a company, delete a document."""

from pathlib import Path
from typing import BinaryIO

from fin.audit.errors import UiActionInvalid
from fin.audit.logger import Run
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.documents import delete_document
from fin.paths import COMPANY_NAME, company_db_path, inbox_dir
from fin.services.result import ActionResult, perform
from fin.status import companies
from fin.ui.uploads import save_uploads

MAX_CODE = 40
MAX_NAME = 120


def upload(
    root: Path, company: str, files: list[tuple[str, BinaryIO]], *, via: str, actor: str
) -> ActionResult:
    """Put files into the company's inbox folder. Nothing is loaded until Ingest runs."""
    inbox = inbox_dir(root, company)

    def body(run: Run) -> list[str]:
        saved = save_uploads(inbox, files)
        for name in saved:
            run.event("ui upload", f"saved {name} to inbox/{company}/", file=f"{company}/{name}")
        return [f"{name} is waiting in inbox/{company}/" for name in saved] + [
            "Nothing is loaded yet: press Ingest."
        ]

    args = {"company": company, "files": [n for n, _ in files], "via": via, "actor": actor}
    return perform(root, "ui upload", args, body)


def create_company(root: Path, code: str, name: str, *, via: str, actor: str) -> ActionResult:
    """Make the company's database and inbox folder and store its name. E008 (no run) for a code
    that is not lower-case letters, digits, - and _, a code in use, or an empty name."""
    code, name = code.strip(), " ".join(name.split())
    if not COMPANY_NAME.match(code) or len(code) > MAX_CODE:
        raise UiActionInvalid(
            f"{code!r} is not a short code: use lower-case letters, digits, - and _ "
            f"(at most {MAX_CODE}), starting with a letter or digit"
        )
    if not name or len(name) > MAX_NAME:
        raise UiActionInvalid(f"give the company's name (at most {MAX_NAME} characters)")
    if code in companies(root):
        raise UiActionInvalid(f"there is a company {code} already; choose another short code")

    def body(run: Run) -> list[str]:
        path = company_db_path(run.root, code)
        ensure_database(path, "company")
        with connect(path, write=True) as con:
            con.execute(
                "INSERT INTO company_profile VALUES (?, ?, ?, ?)",
                [code, name, utcnow(), run.run_id],
            )
        inbox_dir(run.root, code).mkdir(parents=True, exist_ok=True)
        run.event("company", f"created {code}: {name}")
        return [f"created {name} as {code}", f"its files go in inbox/{code}/: upload them here"]

    args = {"code": code, "name": name, "via": via, "actor": actor}
    return perform(root, "company create", args, body)


def delete_documents(
    root: Path, company: str, ids: list[int], *, via: str, actor: str
) -> ActionResult:
    """Delete documents with all their versions, backed up first (`fin.documents`)."""

    def body(run: Run) -> list[str]:
        return [line for doc_id in ids for line in delete_document(run, company, doc_id)]

    args = {"company": company, "ids": ids, "via": via, "actor": actor}
    return perform(root, "document delete", args, body)
