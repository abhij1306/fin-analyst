"""`fin map propose` and `fin review` (PLAN.md Phase 5). The logic is in `fin.map.review`.

`fin review` with no subcommand is the interactive loop; `list`, `confirm` and `reject` are the
same actions with flags, so Claude can run them when the user asks.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import FinError, InvalidMapping
from fin.audit.logger import Run, execute
from fin.ingest import COMPANY_NAME
from fin.map import review

map_app = typer.Typer(no_args_is_help=True, help="Mapping proposals.")
review_app = typer.Typer(
    invoke_without_command=True,
    help="Review unmapped labels: interactive without a subcommand, or use list/confirm/reject.",
)


def _company(value: str | None) -> str | None:
    if value is not None and not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


CompanyOption = Annotated[
    str, typer.Option("--company", "-c", callback=_company, help="Company name, e.g. kestrel.")
]
OptionalCompany = Annotated[
    str | None, typer.Option("--company", "-c", callback=_company, help="Only this company.")
]


@map_app.command("propose")
def map_propose(
    file: Annotated[Path, typer.Argument(help="JSON list of proposals.")],
    company: CompanyOption,
) -> None:
    """Store mapping proposals as pending; nothing is applied. Each proposal is an object with
    label, account, confidence (0 to 1) and reason, plus statement and section if the label is not
    unique in review/<company>/pending.json."""

    def body(run: Run) -> None:
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidMapping(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise InvalidMapping(f"{file}: expected a JSON list of proposals")
        count = review.propose(run, company, proposals)
        typer.echo(f"{count} proposals stored as pending for {company}; run `fin review list`")

    execute("map propose", {"file": file, "company": company}, body)


def _line(item: review.Item) -> str:
    line = f"{item['company']} #{item['id']} [{item['status']}] {item['statement']} / "
    line += f"{item['section']!r} {item['label']!r} ({item['facts']} facts)"
    if item["account"]:
        line += f" -> {item['account']} (confidence {item['confidence']}): {item['reason']}"
    return line


def _items(run: Run, company: str | None, include_rejected: bool = False) -> list[review.Item]:
    """The items of one company (or of every company with a pending file), tagged with it."""
    found = []
    for name in [company] if company else review.companies_with_pending(run.root):
        pending = review.read_pending(run.root, name)
        chosen = pending["items"] if include_rejected else review.open_items(pending)
        found.extend({"company": name, **item} for item in chosen)
    return found


@review_app.callback()
def review_main(ctx: typer.Context, company: OptionalCompany = None) -> None:
    """Without a subcommand: go through the open items one by one."""
    if ctx.invoked_subcommand is not None:
        return

    def body(run: Run) -> None:
        items = _items(run, company)
        typer.echo(f"{len(items)} items to review")
        for item in items:
            if not _ask(run, item):
                break

    execute("review", {"company": company}, body)


def _ask(run: Run, item: review.Item) -> bool:
    """Prompt for one item until it is decided or skipped. False when the user quits."""
    company = item["company"]
    typer.echo(f"\n{_line(item)}\n  e.g. {'; '.join(item['where'])}")
    while True:
        choice = typer.prompt("[c]onfirm, [r]eject, [e]dit account, [s]kip, [q]uit", default="s")
        try:
            if choice == "c":
                account = item["account"] or typer.prompt("account")
            elif choice == "e":
                account = typer.prompt("account")
            elif choice == "r":
                review.reject(run, company, item["id"])
                typer.echo("rejected")
                return True
            elif choice == "s":
                return True
            elif choice == "q":
                return False
            else:
                continue
            scope = typer.prompt("scope (global or company)", default="company")
            facts = review.confirm(run, company, item["id"], account, scope)
            typer.echo(f"confirmed as {account} ({scope}); {facts} facts updated")
            return True
        except FinError as exc:
            typer.echo(str(exc), err=True)
            run.event("review", exc.message, level="error", code=exc.code)
            run.failed += 1


@review_app.command("list")
def review_list(
    company: OptionalCompany = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print items as JSON.")] = False,
    all_items: Annotated[bool, typer.Option("--all", help="Include rejected items.")] = False,
) -> None:
    """List the items waiting for review (from review/<company>/pending.json)."""

    def body(run: Run) -> None:
        items = _items(run, company, all_items)
        run.pending_review = len(items)
        if json_output:
            typer.echo(json.dumps(items, indent=2))
            return
        for item in items:
            typer.echo(_line(item))
        if not items:
            typer.echo("nothing to review")

    execute("review list", {"company": company, "json": json_output}, body)


@review_app.command("confirm")
def review_confirm(
    ids: Annotated[list[int], typer.Argument(help="Item ids from `fin review list`.")],
    company: CompanyOption,
    account: Annotated[
        str | None, typer.Option(help="Account to use instead of the proposed one.")
    ] = None,
    scope: Annotated[
        str, typer.Option(help="global: every company; company: this company only.")
    ] = "company",
) -> None:
    """Confirm items: the label joins the dictionary and the company's matching facts are
    confirmed. The item must have a proposed account, or pass --account."""

    def body(run: Run) -> None:
        for item_id in ids:
            facts = review.confirm(run, company, item_id, account, scope)
            typer.echo(f"#{item_id} confirmed ({scope} scope); {facts} facts updated")

    execute(
        "review confirm",
        {"ids": ids, "company": company, "account": account, "scope": scope},
        body,
    )


@review_app.command("reject")
def review_reject(
    ids: Annotated[list[int], typer.Argument(help="Item ids from `fin review list`.")],
    company: CompanyOption,
) -> None:
    """Reject items: their facts stay unconfirmed."""

    def body(run: Run) -> None:
        for item_id in ids:
            review.reject(run, company, item_id)
            typer.echo(f"#{item_id} rejected")

    execute("review reject", {"ids": ids, "company": company}, body)
