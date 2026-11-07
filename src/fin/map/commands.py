"""`fin map propose|set|accounts` and `fin review` (PLAN.md Phase 5). The logic is in
`fin.map.review`.

`fin review` with no subcommand is the interactive loop; `list`, `confirm` and `reject` are the
same actions with flags, so Claude can run them when the user asks.
"""

import csv
import io
import json
from pathlib import Path
from typing import Annotated

import typer

from fin.audit.errors import FinError, InvalidMapping
from fin.audit.logger import Run, execute
from fin.findings import recheck_held
from fin.ingest import COMPANY_NAME
from fin.map import review
from fin.map.dictionary import STATEMENTS, added_rows
from fin.metrics.export import refresh_analytics

map_app = typer.Typer(
    no_args_is_help=True, help="Mapping proposals, company mappings and the account list."
)
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
    unique in review/<company>/pending.json. When no account fits, account is a new snake_case
    name and new_account is {parent, sign, unit, description}; it is added only on confirm."""

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


StatementOption = Annotated[
    str,
    typer.Option(
        "--statement", "-s", help="balance_sheet, profit_and_loss or cash_flow.", show_default=False
    ),
]


@map_app.command("set")
def map_set(
    company: CompanyOption,
    statement: StatementOption,
    label: Annotated[str, typer.Option("--label", "-l", help="The label as written.")],
    account: Annotated[
        str, typer.Option("--account", "-a", help="Account, see `fin map accounts`.")
    ],
    section: Annotated[
        str, typer.Option(help="Only under this heading (as written); default: any heading.")
    ] = "",
) -> None:
    """Map a label to an account for this company only. Use it to fix the mapping behind a held
    document's findings (`fin findings list`). Loaded and held facts do not change: ingest the
    file again with --force to apply it (the new version is checked; the old one is superseded)."""

    def body(run: Run) -> None:
        if statement not in STATEMENTS:
            raise InvalidMapping(f"statement must be one of {', '.join(STATEMENTS)}")
        outcome = review.set_mapping(run, company, statement, label, account, section)
        where = f" under {section!r}" if section else ""
        typer.echo(f"{company}: {statement} {label!r}{where} -> {account} ({outcome})")

    execute(
        "map set",
        {
            "company": company,
            "statement": statement,
            "label": label,
            "account": account,
            "section": section,
        },
        body,
    )


@map_app.command("accounts")
def map_accounts(
    statement: Annotated[
        str | None, typer.Option("--statement", "-s", help="Only this statement.")
    ] = None,
    added: Annotated[
        bool,
        typer.Option(
            "--added",
            help="Print the accounts and global labels added in review as rows for "
            "templates/dictionary/schedule_iii.csv and labels.csv, to share them.",
        ),
    ] = False,
) -> None:
    """List the dictionary's accounts: account, statement, parent and sign, unit, description.
    Accounts added in review are marked [added]."""

    def body(run: Run) -> None:
        if added:
            accounts, labels = added_rows(run.root)
            text = io.StringIO()
            out = csv.writer(text, lineterminator="\n")
            text.write("# rows for templates/dictionary/schedule_iii.csv\n")
            out.writerows(accounts)
            text.write("# rows for templates/dictionary/labels.csv\n")
            out.writerows(labels)
            typer.echo(text.getvalue(), nl=False)
            return
        for account, st, parent, sign, unit, description, source in review.list_accounts(
            run.root, statement
        ):
            into = f"{'+' if sign == '1' else '-'} {parent}" if parent else "(top line)"
            mark = "[added] " if source == "review" else ""
            typer.echo(f"{account:40} {st:16} {into:42} {unit:16} {mark}{description}")

    execute("map accounts", {"statement": statement, "added": added}, body)


def _line(item: review.Item) -> str:
    line = f"{item['company']} #{item['id']} [{item['status']}] {item['statement']} / "
    line += f"{item['section']!r} {item['label']!r} ({item['facts']} facts)"
    if item["account"]:
        line += f" -> {item['account']} (confidence {item['confidence']}): {item['reason']}"
    new = item.get("new_account")
    if new:
        into = (
            f"{'+' if new['sign'] == 1 else '-'} {new['parent']}" if new["parent"] else "top line"
        )
        line += f"\n  NEW ACCOUNT {item['account']}: {into}, {new['unit']}, {new['description']!r}"
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
                account = None if item["account"] else typer.prompt("account")
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
            typer.echo(
                f"confirmed as {account or item['account']} ({scope}); {facts} facts updated"
            )
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
    name: Annotated[
        str | None, typer.Option(help="New-account proposal: add it under this name instead.")
    ] = None,
    parent: Annotated[
        str | None, typer.Option(help="New-account proposal: add it under this parent instead.")
    ] = None,
) -> None:
    """Confirm items: the label joins the dictionary and the company's matching facts are
    confirmed. The item must have a proposed account, or pass --account. A proposed new account
    is added to the dictionary first (--name and --parent change it; --account uses an existing
    account instead). Held documents are then checked again (a fix that clears their findings
    loads them)."""

    def body(run: Run) -> None:
        for item_id in ids:
            facts = review.confirm(run, company, item_id, account, scope, name, parent)
            typer.echo(f"#{item_id} confirmed ({scope} scope); {facts} facts updated")
        rechecked = recheck_held(run, company)
        for doc in rechecked:
            typer.echo(
                f"held doc_id {doc['doc_id']} {doc['file']} checked again: {doc['open']} open "
                f"findings, {doc['status']}"
            )
        if any(doc["status"] == "loaded" for doc in rechecked):
            refresh_analytics(run)

    execute(
        "review confirm",
        {
            "ids": ids,
            "company": company,
            "account": account,
            "scope": scope,
            "name": name,
            "parent": parent,
        },
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
