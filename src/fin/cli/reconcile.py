"""`fin reconcile <company> [name] [--period]`: run reconciliations (`fin.diligence.recon`,
`fin.reconcile`).

tb_vs_statements: each audited period's trial balance, rolled up by confirmed ledger mapping,
against its audited standalone statement figures; lines at or above the engagement threshold get
draft accountant queries. D4 adds sales_register_vs_gl, purchase_register_vs_gl, gl_vs_tb and
payroll_vs_gl, and D5's bank_vs_invoices and bank_vs_tb: their lines at or above the threshold
are grouped into queries by Claude
(`fin query raise --items`); fx lines are listed, not queried. Nothing is fixed."""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import MatchProposalInvalid
from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.diligence import bank_proposals, recon
from fin.reconcile import NAMES, RECONCILIATIONS, run_all, run_new
from fin.reconcile.format import format_result

MATCH = "match"


def _name(value: str | None) -> str | None:
    if value is not None and value not in (*NAMES, MATCH):
        raise typer.BadParameter(f"use one of: {', '.join((*NAMES, MATCH))}")
    return value


def _text(result: dict) -> str:  # type: ignore[type-arg]
    return recon.format_result(result) if result["name"] == recon.NAME else format_result(result)


@app.command("reconcile")
def reconcile(
    company: Annotated[
        str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")
    ],
    name: Annotated[
        str | None,
        typer.Argument(
            callback=_name,
            help="One reconciliation: " + ", ".join(NAMES) + ", or match (bank match proposals)."
            " Default: every reconciliation whose inputs are loaded.",
        ),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help="Only this period, e.g. FY25 (default: every period the reconciliation applies "
            "to).",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    propose: Annotated[
        Path | None,
        typer.Option(
            "--propose",
            help="match only: a JSON list of matches Claude proposes for what the bank passes "
            "left unmatched: {bank: [[file_id, sheet, row]], invoices: [...], confidence, reason}.",
        ),
    ] = None,
) -> None:
    """Compare two sources for each period. tb_vs_statements: one line per account, differences at
    or above the threshold become DRAFT queries. The others (registers vs Ledger Vouchers, Ledger
    Vouchers vs trial balance, payroll vs Ledger Vouchers): lines at or above the threshold are for
    Claude to group into queries (`fin query raise --items`); fx lines are listed, not queried.
    Differences are queried, never fixed. `match` lists the bank match proposals (--propose FILE
    stores Claude's; the user confirms them with `fin review confirm --matches`)."""

    def body(run: Run) -> None:
        if name == MATCH:
            return _match(run, company, propose, json_output)
        if propose is not None:
            raise typer.BadParameter("--propose applies to `match` only")
        not_run: list[str] = []
        if name is None:
            results, not_run = run_all(run, company, period)
        elif name in RECONCILIATIONS:
            results = run_new(run, company, name, period)
        else:
            results = recon.tb_vs_statements(run, company, period)
        if json_output:
            typer.echo(json.dumps(results, indent=2, default=str))
        else:
            typer.echo("\n".join(_text(result) for result in results))
        for message in not_run:
            typer.echo(f"not run: {message}", err=True)

    args = {"company": company, "name": name, "period": period, "propose": propose}
    execute("reconcile", args, body)


def _match(run: Run, company: str, file: Path | None, json_output: bool) -> None:
    if file is not None:
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MatchProposalInvalid(f"cannot read proposals from {file}: {exc}") from exc
        ids = bank_proposals.propose(run, company, proposals)
        if json_output:
            typer.echo(json.dumps({"proposal_ids": ids}))
            return
        typer.echo(
            f"{len(ids)} match proposals stored for {company} (#{', #'.join(map(str, ids))}); the "
            f"user reviews them: `fin review list {company} --matches`"
        )
        return
    rows = bank_proposals.list_proposals(run, company, waiting=False)
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(bank_proposals.format_proposal, rows)) or "no match proposals")
