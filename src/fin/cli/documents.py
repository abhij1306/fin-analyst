"""`fin document delete`: the Files screen's Delete (docs/specs/SPEC-03) from the command line."""

from typing import Annotated

import typer

from fin.actor import from_environment
from fin.cli.core import app, check_company
from fin.paths import data_dir
from fin.services.files import delete_documents
from fin.services.result import show

document_app = typer.Typer(no_args_is_help=True, help="Documents of a company: delete one.")
app.add_typer(document_app, name="document")


@document_app.command("delete")
def document_delete(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    ids: Annotated[list[int], typer.Argument(help="Document ids (doc_id) from `fin status`.")],
) -> None:
    """Delete documents with all their versions: their rows, archived original, notes, a reading
    no other document uses and the inbox copy, after backing them up to backups/<run_id>/."""

    show(delete_documents(data_dir(), company, ids, via="cli", actor=from_environment()))
