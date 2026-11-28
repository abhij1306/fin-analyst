"""`fin query export <co> [--brief]`: the command; the logic is in `export.py`, run through the
service the web UI calls too (`fin.services.diligence`)."""

from typing import Annotated, Any

import typer

from fin.actor import from_environment
from fin.paths import data_dir
from fin.services import diligence as services
from fin.services.result import show


def register(query_app: typer.Typer, company_arg: Any) -> None:
    """Add `export` to the `fin query` commands (`company_arg` is the company argument type)."""

    @query_app.command("export")
    def query_export(
        company: company_arg,  # type: ignore[valid-type]
        brief: Annotated[
            bool, typer.Option("--brief", help="A 1-2 page Word brief per query instead.")
        ] = False,
    ) -> None:
        """Write the approved queries for the accountant to the data folder (real names, not
        tokens): a marked-up workbook and a draft email, or with --brief a Word brief per query.
        Prints only the file paths. Send them yourself, then `fin query sent`."""

        show(
            services.export_queries(
                data_dir(), company, brief=brief, via="cli", actor=from_environment()
            )
        )
