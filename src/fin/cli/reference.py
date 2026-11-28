"""`fin reference add|lines|propose|confirm|reject|compare|query|adjust` (SPEC-10): a third party's
figures beside the company's, never as company data. The logic is in `fin.reference`."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.errors import ReferenceLineInvalid
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.config import load_config
from fin.diligence import adjustments, queries
from fin.diligence.cli_options import rupees_option
from fin.ingest import UNIT_ANSWERS, GivenUnit
from fin.paths import data_dir
from fin.reference import compare, handoff, lines
from fin.reference.store import add_reference
from fin.services import decisions
from fin.services.result import show

reference_app = typer.Typer(
    no_args_is_help=True,
    help="Third-party reference figures (a banker's or adviser's databook) beside fin's.",
)
app.add_typer(reference_app, name="reference")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]
Ids = Annotated[list[int], typer.Argument(help="Line ids from fin reference lines.")]
ValueId = Annotated[int, typer.Argument(help="The figure's #id from fin reference compare.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _unit(value: str | None) -> str | None:
    if value is not None and value not in UNIT_ANSWERS:
        raise typer.BadParameter(f"use one of: {', '.join(UNIT_ANSWERS)}")
    return value


@reference_app.command("add")
def add_command(
    company: Company,
    path: Annotated[Path, typer.Argument(help="The reference workbook or PDF.")],
    source: Annotated[str, typer.Option("--source", help="Who the figures come from.")],
    unit: Annotated[
        str | None,
        typer.Option(
            callback=_unit, help=f"For sheets that name no unit: {', '.join(UNIT_ANSWERS)}."
        ),
    ] = None,
    reading: Annotated[
        Path | None, typer.Option(help="The assistant's reading of what fin cannot read.")
    ] = None,
) -> None:
    """Store a third party's figures apart from the company's: they never become facts. Lines the
    dictionary maps are compared at once; the others wait for a mapping (fin reference lines)."""

    def body(run: Run) -> None:
        given = GivenUnit(UNIT_ANSWERS[unit], from_environment()) if unit else None
        added = add_reference(
            run, load_config(run.root), company, path, source, unit=given, reading=reading
        )
        typer.echo(f"{company}/{path.name}: {added.line}")

    args = {"company": company, "path": path, "source": source, "unit": unit, "reading": reading}
    execute("reference add", args, body)


@reference_app.command("lines")
def lines_command(
    company: Company,
    all_lines: Annotated[bool, typer.Option("--all", help="Every line, not only waiting.")] = False,
    json_output: JsonOption = False,
) -> None:
    """The reference lines waiting for a mapping (--all: every line)."""

    def body(run: Run) -> None:
        rows = lines.list_lines(run.root, company, not all_lines)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            typer.echo("\n".join(map(lines.format_line, rows)) or "no reference line waiting")

    execute("reference lines", {"company": company, "all": all_lines}, body)


@reference_app.command("propose")
def propose_command(
    company: Company,
    file: Annotated[
        Path, typer.Argument(help="JSON list: {line, account or metric, confidence, reason}.")
    ],
) -> None:
    """Store the assistant's mappings for waiting lines; the user confirms or rejects them."""

    def body(run: Run) -> None:
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceLineInvalid(f"cannot read proposals from {file}: {exc}") from exc
        ids = lines.propose(run, company, proposals)
        typer.echo(f"{len(ids)} reference lines proposed; the user confirms or rejects them")

    execute("reference propose", {"company": company, "file": file}, body)


@reference_app.command("confirm")
def confirm_command(company: Company, ids: Ids) -> None:
    """Confirm proposed lines: they are compared from now on. Only the user does this."""
    show(
        decisions.decide_reference_lines(
            data_dir(), company, ids, "confirmed", via="cli", actor=from_environment()
        )
    )


@reference_app.command("reject")
def reject_command(company: Company, ids: Ids) -> None:
    """Reject lines: they are not compared. Only the user does this."""
    show(
        decisions.decide_reference_lines(
            data_dir(), company, ids, "rejected", via="cli", actor=from_environment()
        )
    )


@reference_app.command("compare")
def compare_command(
    company: Company,
    all_rows: Annotated[
        bool, typer.Option("--all", help="Every figure, not only differences.")
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Each mapped reference figure beside fin's, in the reference's unit, with both sources:
    differences at or above the threshold first."""

    def body(run: Run) -> None:
        data = compare.comparison(run.root, company)
        if json_output:
            typer.echo(json.dumps(data, indent=2, default=str))
        else:
            typer.echo(compare.format_comparison(company, data, all_rows))

    execute("reference compare", {"company": company, "all": all_rows}, body)


@reference_app.command("query")
def query_command(
    company: Company,
    value_id: ValueId,
    question: Annotated[str, typer.Option("--question", help="The query's wording.")],
) -> None:
    """Draft a query on a difference, with both sources; the user approves it."""

    def body(run: Run) -> None:
        typer.echo(queries.format_query(handoff.raise_query(run, company, value_id, question)))

    execute("reference query", {"company": company, "value_id": value_id}, body)


@reference_app.command("adjust")
def adjust_command(
    company: Company,
    value_id: ValueId,
    category: Annotated[str, typer.Option("--category", help="An adjustment category.")],
    description: Annotated[str, typer.Option("--description", help="What it adjusts.")],
    amount: Annotated[
        Decimal,
        typer.Option(
            "--amount", parser=rupees_option, metavar="RUPEES", help="Signed effect on EBITDA."
        ),
    ],
) -> None:
    """Propose an adjustment from a difference, with both sources as evidence; the user accepts
    or rejects it."""

    def body(run: Run) -> None:
        row = handoff.propose_adjustment(
            run, company, value_id, category=category, description=description, amount=amount
        )
        typer.echo(adjustments.format_adjustment(row))

    args = {"company": company, "value_id": value_id, "category": category, "amount": amount}
    execute("reference adjust", args | {"description": description}, body)
