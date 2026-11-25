"""`fin fdd finding` (docs/fdd.md F8): the findings of the FDD report (`fin.fdd.findings`). Claude
proposes, updates and drops draft findings; only the user marks one final (`final`, or `final
--undo` to return it to draft). The report is `fin report <co> --fdd`."""

import json
from typing import Annotated, Any

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company
from fin.fdd import findings

fdd_app = typer.Typer(no_args_is_help=True, help="FDD: the findings of the FDD report.")
finding_app = typer.Typer(
    no_args_is_help=True, help="FDD findings: Claude proposes, only the user marks one final."
)
app.add_typer(fdd_app, name="fdd")
fdd_app.add_typer(finding_app, name="finding")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]
Ids = Annotated[list[int], typer.Argument(help="Finding ids (`fin fdd finding list`).")]


def _opt(flag: str, text: str) -> Any:
    return typer.Option(flag, help=text)


Area = Annotated[str | None, _opt("--area", "One of " + ", ".join(findings.AREAS) + ".")]
Title = Annotated[str | None, _opt("--title", "A short title, no typed numbers.")]
Observation = Annotated[str | None, _opt("--observation", "What was found, neutrally.")]
Implication = Annotated[str | None, _opt("--implication", "What it means for the deal.")]
Impact = Annotated[str | None, _opt("--impact", "One of " + ", ".join(findings.IMPACTS) + ".")]
Severity = Annotated[str | None, _opt("--severity", "high, medium or low.")]
Deal = Annotated[str | None, _opt("--deal", "One of " + ", ".join(findings.DEALS) + ".")]
Evidence = Annotated[str | None, _opt("--evidence", "References: file + page, fin output, query.")]
Adjustment = Annotated[int | None, _opt("--adjustment", "Amount from this adjustment.")]
Item = Annotated[int | None, _opt("--netdebt-item", "Amount from this net debt item.")]
Recon = Annotated[int | None, _opt("--recon", "Amount from this reconciliation run.")]
Query = Annotated[int | None, _opt("--query", "Management response (and amount) from this query.")]


def _fields(**given: Any) -> dict[str, Any]:
    names = {"impact": "impact_type", "deal": "deal_consideration", "adjustment": "adj_id",
             "item": "netdebt_item_id", "recon": "recon_id", "query": "query_id"}  # fmt: skip
    return {names.get(k, k): v for k, v in given.items() if v is not None}


def _echo(rows: list[dict[str, Any]], empty: str = "no FDD findings") -> None:
    typer.echo("\n".join(map(findings.format_finding, rows)) if rows else empty)


@finding_app.command("propose")
def propose(
    company: Company,
    area: Area = None,
    title: Title = None,
    observation: Observation = None,
    severity: Severity = None,
    impact: Impact = None,
    deal: Deal = None,
    evidence: Evidence = None,
    implication: Implication = None,
    adjustment: Adjustment = None,
    item: Item = None,
    recon: Recon = None,
    query: Query = None,
) -> None:
    """Propose a draft finding with evidence. The amount comes only from a linked adjustment, net
    debt item, reconciliation run or query; --query also gives the management response."""
    given = dict(locals())
    del given["company"]
    required = ("area", "title", "observation", "severity", "impact", "deal", "evidence")
    missing = [f"--{name}" for name in required if given[name] is None]
    fields = _fields(**given)
    if missing:
        raise typer.BadParameter(f"give {', '.join(missing)}")
    execute(
        "fdd finding propose",
        {"company": company, **{k: v for k, v in fields.items() if k.endswith("_id")}},
        lambda run: _echo([findings.propose(run, company, fields)]),
    )


@finding_app.command("update")
def update(
    company: Company,
    finding_id: Annotated[int, typer.Argument(help="The draft finding's id.")],
    area: Area = None,
    title: Title = None,
    observation: Observation = None,
    severity: Severity = None,
    impact: Impact = None,
    deal: Deal = None,
    evidence: Evidence = None,
    implication: Implication = None,
    adjustment: Adjustment = None,
    item: Item = None,
    recon: Recon = None,
    query: Query = None,
) -> None:
    """Change a draft finding (a final one: the user returns it to draft first)."""
    given = dict(locals())
    del given["company"], given["finding_id"]
    fields = _fields(**given)
    execute(
        "fdd finding update",
        {"company": company, "finding_id": finding_id, "fields": sorted(fields)},
        lambda run: _echo([findings.update(run, company, finding_id, fields)]),
    )


@finding_app.command("list")
def list_command(
    company: Company,
    every: Annotated[bool, typer.Option("--all", help="Dropped ones too.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """The open engagement's FDD findings, by severity."""

    def body(run: Run) -> None:
        rows = findings.list_findings(run, company, every)
        if json_output:
            typer.echo(json.dumps(rows, indent=2, default=str))
        else:
            _echo(rows)

    execute("fdd finding list", {"company": company, "all": every}, body)


@finding_app.command("drop")
def drop(company: Company, ids: Ids) -> None:
    """Drop draft findings (kept as dropped, out of the report)."""
    execute(
        "fdd finding drop",
        {"company": company, "ids": ids},
        lambda run: _echo(findings.set_status(run, company, ids, "dropped")),
    )


@finding_app.command("final")
def final(
    company: Company,
    ids: Ids,
    undo: Annotated[bool, typer.Option("--undo", help="Return final findings to draft.")] = False,
) -> None:
    """Mark draft findings final (they appear in the report), or with --undo return final ones to
    draft. Only when the user says so."""
    execute(
        "fdd finding final",
        {"company": company, "ids": ids, "undo": undo},
        lambda run: _echo(findings.set_status(run, company, ids, "draft" if undo else "final")),
    )
