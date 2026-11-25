"""`fin map propose|set|accounts` and `fin review list|confirm|reject`. The logic is in
`fin.map.review`."""

import csv
import io
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run, execute
from fin.diligence import bank_proposals, ledgers
from fin.diligence.ledgers import list_ledgers
from fin.findings import recheck_held
from fin.map import review
from fin.map.dictionary import STATEMENTS, added_rows
from fin.metrics.export import refresh_analytics
from fin.paths import COMPANY_NAME, companies_dir, company_db_path

map_app = typer.Typer(
    no_args_is_help=True, help="Mapping proposals, company mappings and the account list."
)
review_app = typer.Typer(
    no_args_is_help=True, help="Review mapping proposals: list, confirm or reject them."
)


def _company(value: str | None) -> str | None:
    if value is not None and not COMPANY_NAME.match(value):
        raise typer.BadParameter("use lower-case letters, digits, '-' and '_' (e.g. kestrel)")
    return value


CompanyArg = Annotated[
    str, typer.Argument(callback=_company, help="Company name, e.g. kestrel.", show_default=False)
]
LedgersOption = Annotated[
    bool, typer.Option("--ledgers", help="Tally ledgers (`fin map ledgers`) instead of labels.")
]
MatchesOption = Annotated[
    bool,
    typer.Option("--matches", help="Bank match proposals (`fin reconcile <co> match`) instead."),
]
OptionalCompany = Annotated[
    str | None, typer.Argument(callback=_company, help="Only this company.")
]


@map_app.command("propose")
def map_propose(
    company: CompanyArg,
    file: Annotated[Path, typer.Argument(help="JSON list of proposals.")],
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
        typer.echo(
            f"{count} proposals stored as pending for {company}; run `fin review list {company}`"
        )

    execute("map propose", {"file": file, "company": company}, body)


StatementOption = Annotated[
    str,
    typer.Option(
        "--statement", "-s", help="balance_sheet, profit_and_loss or cash_flow.", show_default=False
    ),
]


@map_app.command("set")
def map_set(
    company: CompanyArg,
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
            into = _placement(sign == "1", parent) if parent else "(top line)"
            mark = "[added] " if source == "review" else ""
            typer.echo(f"{account:40} {st:16} {into:42} {unit:16} {mark}{description}")

    execute("map accounts", {"statement": statement, "added": added}, body)


def _placement(adds: bool, parent: str) -> str:
    """How an account rolls into its parent: "+ parent" (adds) or "- parent" (subtracts)."""
    sign = "+" if adds else "-"
    return f"{sign} {parent}"


def _line(item: review.Item) -> str:
    line = f"{item['company']} #{item['id']} [{item['status']}] {item['statement']} / "
    line += f"{item['section']!r} {item['label']!r} ({item['facts']} facts)"
    if item["account"]:
        line += f" -> {item['account']} (confidence {item['confidence']}): {item['reason']}"
    new = item.get("new_account")
    if new:
        into = _placement(new["sign"] == 1, new["parent"]) if new["parent"] else "top line"
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


@review_app.command("list")
def review_list(
    company: OptionalCompany = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print items as JSON.")] = False,
    all_items: Annotated[bool, typer.Option("--all", help="Include rejected items.")] = False,
    ledger_items: LedgersOption = False,
    match_items: MatchesOption = False,
) -> None:
    """List the items waiting for review (from review/<company>/pending.json), or with --ledgers
    the Tally ledgers waiting for an account (--all: every ledger), or with --matches the bank
    match proposals waiting for the user."""

    def body(run: Run) -> None:
        if match_items:
            if company is None:
                raise typer.BadParameter("--matches needs a company")
            rows = bank_proposals.list_proposals(run, company, not all_items)
            if json_output:
                typer.echo(json.dumps(rows, indent=2, default=str))
            else:
                typer.echo(
                    "\n".join(map(bank_proposals.format_proposal, rows)) or "nothing waiting"
                )
            return
        if ledger_items:
            rows = []
            for name in [company] if company else _ledger_companies(run):
                rows += [{"company": name, **row} for row in list_ledgers(run, name, not all_items)]
            _echo_ledgers(rows, json_output)
            return
        items = _items(run, company, all_items)
        run.pending_review = len(items)
        if json_output:
            typer.echo(json.dumps(items, indent=2))
            return
        for item in items:
            typer.echo(_line(item))
        if not items:
            typer.echo("nothing to review")
        has_db = company and company_db_path(run.root, company).is_file()
        if has_db and (waiting := len(list_ledgers(run, company, True))):
            typer.echo(
                f"{waiting} ledgers waiting for review: `fin review list {company} --ledgers`"
            )

    args = {
        "company": company,
        "json": json_output,
        "ledgers": ledger_items,
        "matches": match_items,
    }
    execute("review list", args, body)


@review_app.command("confirm")
def review_confirm(
    company: CompanyArg,
    ids: Annotated[list[int], typer.Argument(help="Item ids from `fin review list`.")],
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
    ledger_items: LedgersOption = False,
    match_items: MatchesOption = False,
) -> None:
    """Confirm items, all or nothing: the label joins the dictionary and the company's matching
    facts are confirmed. The item must have a proposed account, or pass --account. A proposed new
    account is added to the dictionary first (--name and --parent change it; --account uses an
    existing account instead). Held documents and the documents whose facts changed are then
    checked again (a fix that clears a held document's findings loads it; a loaded document that
    now fails a check is held). With --ledgers the ids are ledger ids: their proposed accounts
    (or --account) are confirmed; only the user does this."""

    def body(run: Run) -> None:
        if match_items:
            for i in bank_proposals.decide(run, company, ids, "confirmed"):
                typer.echo(f"match #{i} confirmed; applied by the next bank_vs_invoices run")
            return
        if ledger_items:
            if scope != "company" or name or parent:
                raise typer.BadParameter("--scope, --name and --parent do not apply to --ledgers")
            for row in ledgers.confirm(run, company, ids, account):
                typer.echo(
                    f"ledger #{row['ledger_id']} {row['ledger']!r} confirmed as {row['account']}"
                )
            return
        confirmed, changed = review.confirm(run, company, ids, account, scope, name, parent)
        for item_id, facts in confirmed:
            typer.echo(f"#{item_id} confirmed ({scope} scope); {facts} facts updated")
        rechecked = recheck_held(run, company, changed)
        for doc in rechecked:
            typer.echo(
                f"doc_id {doc['doc_id']} {doc['file']} checked again: {doc['open']} open "
                f"findings, {doc['status']}"
            )
        if changed or any(doc["status"] == "loaded" for doc in rechecked):
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
            "ledgers": ledger_items,
            "matches": match_items,
        },
        body,
    )


@review_app.command("reject")
def review_reject(
    company: CompanyArg,
    ids: Annotated[list[int], typer.Argument(help="Item ids from `fin review list`.")],
    ledger_items: LedgersOption = False,
    match_items: MatchesOption = False,
) -> None:
    """Reject items, all or nothing: their facts stay unconfirmed. With --ledgers the ids are
    ledger ids: the ledgers stay unmapped. With --matches they are match proposal ids."""

    def body(run: Run) -> None:
        if match_items:
            for i in bank_proposals.decide(run, company, ids, "rejected"):
                typer.echo(f"match #{i} rejected")
            return
        if ledger_items:
            for row in ledgers.reject(run, company, ids):
                typer.echo(f"ledger #{row['ledger_id']} {row['ledger']!r} rejected")
            return
        review.reject(run, company, ids)
        for item_id in dict.fromkeys(ids):
            typer.echo(f"#{item_id} rejected")

    args = {"ids": ids, "company": company, "ledgers": ledger_items, "matches": match_items}
    execute("review reject", args, body)


@map_app.command("ledgers")
def map_ledgers(
    company: CompanyArg,
    file: Annotated[
        Path | None,
        typer.Argument(help="JSON list of proposals: {ledger, account, confidence, reason}."),
    ] = None,
) -> None:
    """Tally ledgers -> Schedule III accounts. Without FILE: list the ledgers waiting for an
    account. With FILE: store Claude's proposals; nothing is confirmed (the user confirms them:
    `fin review confirm <co> <ids> --ledgers`). Party ledgers go by their token."""

    def body(run: Run) -> None:
        if file is None:
            _echo_ledgers(list_ledgers(run, company, True), False)
            return
        try:
            proposals = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidMapping(f"cannot read proposals from {file}: {exc}") from exc
        if not isinstance(proposals, list):
            raise InvalidMapping(f"{file}: expected a JSON list of proposals")
        count = ledgers.propose(run, company, proposals)
        typer.echo(
            f"{count} ledger proposals stored for {company}; the user reviews them: "
            f"`fin review list {company} --ledgers`"
        )

    execute("map ledgers", {"company": company, "file": file}, body)


def _ledger_companies(run: Run) -> list[str]:
    return sorted(db.stem for db in companies_dir(run.root).glob("*.duckdb"))


def _echo_ledgers(rows: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(ledgers.format_ledger, rows)) if rows else "no ledgers waiting")
