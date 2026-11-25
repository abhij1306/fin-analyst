"""`fin disclosures load|list|related` (FDD F5, `fin.disclosures`): Claude's reading of the notes to
accounts and legal and tax notices, the schedules per kind, and the related party links. The
comparison with the books is `fin reconcile <co> rpt_vs_books`; the user confirms links with
`fin review confirm <co> <ids> --related`."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import RelatedPartyLinkInvalid
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.disclosures import load, related
from fin.disclosures.reading import KINDS

disclosures_app = typer.Typer(
    no_args_is_help=True,
    help="Contingencies, commitments, notices and related parties read from the notes (FDD F5).",
)
app.add_typer(disclosures_app, name="disclosures")
CompanyArg = Annotated[
    str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _echo(rows: object, json_output: bool, text: str) -> None:
    typer.echo(json.dumps(rows, indent=2, default=str) if json_output else text)


@disclosures_app.command("load")
def load_command(
    company: CompanyArg,
    file: Annotated[Path, typer.Argument(help="The document the reading was read from.")],
    reading: Annotated[
        Path, typer.Option("--reading", help="CSV Claude wrote from the document (file + page).")
    ],
    json_output: JsonOption = False,
) -> None:
    """Load Claude's reading of notes to accounts or a legal or tax notice: one row per item
    (kind, period, nature, page; counterparty, amount, unit, forum, stage, assessment_year,
    demand, paid_under_protest, relationship, books, ledger). Names are masked on load. Loading the
    same document again replaces its rows. Whether a Claude-read item is trusted is the user's
    decision."""
    if not file.is_file() or not reading.is_file():
        raise typer.BadParameter("the document and the reading must be files that exist")

    def body(run: Run) -> None:
        rows = load.load(run, company, file, reading)
        _echo(rows, json_output, load.format_schedules(rows))

    execute("disclosures load", {"company": company, "file": file, "reading": reading}, body)


@disclosures_app.command("list")
def list_command(
    company: CompanyArg,
    kind: Annotated[
        str | None, typer.Option("--kind", help="Only this schedule: " + ", ".join(KINDS) + ".")
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """The schedules: every item read for the open engagement, by kind, with its file and page."""
    if kind is not None and kind not in KINDS:
        raise typer.BadParameter(f"use one of: {', '.join(KINDS)}")

    def body(run: Run) -> None:
        rows = load.list_disclosures(run, company, kind)
        _echo(rows, json_output, load.format_schedules(rows))

    execute("disclosures list", {"company": company, "kind": kind}, body)


@disclosures_app.command("related")
def related_command(
    company: CompanyArg,
    file: Annotated[
        Path | None,
        typer.Argument(
            help="JSON list of link proposals: {party, kind, target, confidence, reason}."
        ),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Related parties (tokens) and what each is in the books: fin's matches by token and the links
    the user confirmed. With FILE: store Claude's link proposals (kind customer, vendor or ledger);
    nothing is confirmed (the user confirms them: `fin review confirm <co> <ids> --related`)."""

    def body(run: Run) -> None:
        if file is None:
            rows = related.parties(run, company)
            text = "\n".join(map(related.format_party, rows)) or "no related parties"
            return _echo(rows, json_output, text)
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RelatedPartyLinkInvalid(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise RelatedPartyLinkInvalid(f"{file}: expected a JSON list of proposals")
        count = related.propose(run, company, proposals)
        typer.echo(
            f"{count} related party link proposals stored for {company}; the user reviews them: "
            f"`fin review list {company} --related`"
        )

    execute("disclosures related", {"company": company, "file": file}, body)
