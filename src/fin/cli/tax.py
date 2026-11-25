"""`fin map tax`: tax ledger classes (docs/fdd.md F6). Review is `fin review list|confirm|reject
--tax` (`fin.map.commands`); the logic is in `fin.fdd.tax_ledgers`."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import TaxLedgerInvalid
from fin.audit.logger import Run, execute
from fin.fdd import tax_ledgers
from fin.map.commands import CompanyArg, map_app


@map_app.command("tax")
def map_tax(
    company: CompanyArg,
    file: Annotated[
        Path | None,
        typer.Argument(help="JSON list of proposals: {ledger, class, confidence, reason}."),
    ] = None,
) -> None:
    """Tax ledger classes: gst_output, gst_input, rcm, tds, income_tax, mat_credit. Without FILE:
    list the tax ledgers proposed or confirmed and the trial balance ledgers with no class. With
    FILE: store Claude's proposals; nothing is confirmed (the user confirms them:
    `fin review confirm <co> <ids> --tax`)."""

    def body(run: Run) -> None:
        if file is None:
            rows = tax_ledgers.list_rows(run, company)
            typer.echo("\n".join(map(tax_ledgers.format_row, rows)) or "no tax ledgers yet")
            rest = tax_ledgers.unclassified(run, company)
            typer.echo(f"{len(rest)} trial balance ledgers with no tax class:")
            for row in rest:
                typer.echo(f"  {row['ledger']!r} ({row['group_name'] or 'no group'})")
            return
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaxLedgerInvalid(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise TaxLedgerInvalid(f"{file}: expected a JSON list of proposals")
        count = tax_ledgers.propose(run, company, proposals)
        typer.echo(
            f"{count} tax ledger proposals stored for {company}; the user reviews them: "
            f"`fin review list {company} --tax`"
        )

    execute("map tax", {"company": company, "file": file}, body)
