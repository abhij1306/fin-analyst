"""`fin query checklist <co>`: the inquiry topics with their queries and status, and the standard
questions on the topics with none (docs/fdd.md F2). The logic is in `inquiries.py`."""

import json
from typing import Annotated, Any

import typer

from fin.audit.logger import Run, execute
from fin.diligence import inquiries


def register(query_app: typer.Typer, company_arg: Any) -> None:
    """Add `checklist` to the `fin query` commands (`company_arg` is the company argument type)."""

    @query_app.command("checklist")
    def query_checklist(
        company: company_arg,  # type: ignore[valid-type]
        json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    ) -> None:
        """Each inquiry topic with its queries and their status; the topics with none show the
        standard FDD questions for Claude to adapt and draft (`fin query raise --topic`)."""

        def body(run: Run) -> None:
            data = inquiries.checklist(run, company)
            typer.echo(
                json.dumps(data, indent=2, default=str)
                if json_output
                else inquiries.format_checklist(data)
            )

        execute("query checklist", {"company": company, "json": json_output}, body)
