"""`fin map propose|set|accounts` and `fin review list|confirm|reject`. The logic is in
`fin.map.review`."""

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from fin.actor import from_environment
from fin.audit.errors import DependencyMissing, InvalidMapping
from fin.audit.logger import Run, execute
from fin.db.conn import no_company_message
from fin.diligence import bank_proposals, customers, ledgers
from fin.diligence.customers import list_customers
from fin.diligence.ledgers import list_ledgers
from fin.disclosures import related
from fin.fdd import tax_ledgers
from fin.map import review, review_netdebt
from fin.map.dictionary import STATEMENTS, added_rows
from fin.paths import COMPANY_NAME, companies_dir, company_db_path, data_dir
from fin.services import decisions
from fin.services.result import show

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
CustomersOption = Annotated[
    bool,
    typer.Option("--customers", help="Customer sectors (`fin map customers`) instead of labels."),
]
TaxOption = Annotated[
    bool, typer.Option("--tax", help="Tax ledger classes (`fin map tax`) instead of labels.")
]
TaxClassOption = Annotated[
    str | None, typer.Option(help="--tax: the class to use instead of the proposed one.")
]
NetdebtOption = Annotated[
    bool, typer.Option("--netdebt", help="Net debt classes (`fin netdebt propose`) instead.")
]
RelatedOption = Annotated[
    bool,
    typer.Option("--related", help="Related party links (`fin disclosures related`) instead."),
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
        outcome = review.set_mapping(
            run, company, statement, label, account, section, actor=from_environment()
        )
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


def _list_matches(run: Run, company: str, all_items: bool, json_output: bool) -> None:
    rows = bank_proposals.list_proposals(run, company, not all_items)
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(bank_proposals.format_proposal, rows)) or "nothing waiting")


def _rows_by_company(
    run: Run, company: str | None, all_items: bool, lister: Callable[[Run, str, bool], list[Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in [company] if company else _ledger_companies(run):
        rows += [{"company": name, **row} for row in lister(run, name, not all_items)]
    return rows


def _list_review_items(run: Run, company: str | None, all_items: bool, json_output: bool) -> None:
    if company and not company_db_path(run.root, company).is_file():
        raise DependencyMissing(no_company_message(company))
    items = _items(run, company, all_items)
    run.pending_review = len(items)
    if json_output:
        typer.echo(json.dumps(items, indent=2))
        return
    for item in items:
        typer.echo(_line(item))
    if not items:
        typer.echo("nothing to review")
    if company and company_db_path(run.root, company).is_file():
        _echo_waiting(run, company)


def _echo_waiting(run: Run, company: str) -> None:
    if waiting := len(list_ledgers(run, company, True)):
        typer.echo(f"{waiting} ledgers waiting for review: `fin review list {company} --ledgers`")
    if waiting := len(list_customers(run, company, True)):
        typer.echo(
            f"{waiting} customers waiting for a sector: `fin review list {company} --customers`"
        )


def _need_company(company: str | None, flag: str) -> str:
    if company is None:
        raise typer.BadParameter(f"{flag} needs a company")
    return company


def _list_related(run: Run, company: str | None, all_items: bool, json_output: bool) -> None:
    rows = related.list_links(run, _need_company(company, "--related"), not all_items)
    text = "\n".join(map(related.format_link, rows)) or "no related party links waiting"
    typer.echo(json.dumps(rows, indent=2, default=str) if json_output else text)


@review_app.command("list")
def review_list(
    company: OptionalCompany = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print items as JSON.")] = False,
    all_items: Annotated[bool, typer.Option("--all", help="Include rejected items.")] = False,
    ledger_items: LedgersOption = False,
    match_items: MatchesOption = False,
    customer_items: CustomersOption = False,
    tax_items: TaxOption = False,
    related_items: RelatedOption = False,
    netdebt_items: NetdebtOption = False,
) -> None:
    """List the items waiting for review (from review/<company>/pending.json), or with --ledgers
    the Tally ledgers waiting for an account (--all: every ledger), or with --customers the
    customers waiting for a sector, or with --matches the bank match proposals waiting for the
    user, or with --tax the tax ledger classes waiting (--all: every one), or with --related the
    related party links Claude proposed (--all: every link), or with --netdebt the net debt
    class proposals."""

    def body(run: Run) -> None:
        kinds = (ledger_items, match_items, customer_items, tax_items, related_items)
        if netdebt_items:
            names = [company] if company else _ledger_companies(run)
            review_netdebt.list_classes(run, names, all_items, json_output, any(kinds))
        elif related_items:
            _list_related(run, company, all_items, json_output)
        elif match_items:
            _list_matches(run, _need_company(company, "--matches"), all_items, json_output)
        elif customer_items:
            _echo_customers(_rows_by_company(run, company, all_items, list_customers), json_output)
        elif tax_items:
            _echo_tax(_rows_by_company(run, company, all_items, tax_ledgers.list_rows), json_output)
        elif ledger_items:
            _echo_ledgers(_rows_by_company(run, company, all_items, list_ledgers), json_output)
        else:
            _list_review_items(run, company, all_items, json_output)

    args = {
        "company": company,
        "json": json_output,
        "ledgers": ledger_items,
        "matches": match_items,
        "customers": customer_items,
        "tax": tax_items,
        "related": related_items,
        "netdebt": netdebt_items,
    }
    execute("review list", args, body)


def _confirm_matches(run: Run, company: str, ids: list[int]) -> None:
    for i in bank_proposals.decide(run, company, ids, "confirmed"):
        typer.echo(f"match #{i} confirmed; applied by the next bank_vs_invoices run")


def _decide_related(run: Run, company: str, ids: list[int], status: str) -> None:
    for row in related.decide(run, company, ids, status, actor=from_environment()):
        typer.echo(
            f"link #{row['link_id']} {row['party']} -> {row['link_kind']} {row['target']} {status}"
        )


def _confirm_customers(
    run: Run, company: str, ids: list[int], sector: str | None, other_options: bool
) -> None:
    if other_options:
        raise typer.BadParameter("with --customers use only --sector")
    for crow in customers.confirm(run, company, ids, sector, actor=from_environment()):
        typer.echo(
            f"customer #{crow['customer_id']} {crow['customer']} confirmed as {crow['sector']}"
        )


def _confirm_tax(
    run: Run, company: str, ids: list[int], tax_class: str | None, other_options: bool
) -> None:
    if other_options:
        raise typer.BadParameter("with --tax use only --tax-class")
    for row in tax_ledgers.confirm(run, company, ids, tax_class, actor=from_environment()):
        typer.echo(
            f"tax ledger #{row['tax_ledger_id']} {row['ledger']!r} confirmed as {row['tax_class']}"
        )


def _confirm_ledgers(
    run: Run, company: str, ids: list[int], account: str | None, other_options: bool
) -> None:
    if other_options:
        raise typer.BadParameter("--scope, --name and --parent do not apply to --ledgers")
    for row in ledgers.confirm(run, company, ids, account, actor=from_environment()):
        typer.echo(f"ledger #{row['ledger_id']} {row['ledger']!r} confirmed as {row['account']}")


def _check_unused(tax_class: str | None = None, sector: str | None = None) -> None:
    """Usage errors for options that belong to another kind of item."""
    if tax_class is not None:
        raise typer.BadParameter("--tax-class applies to --tax")
    if sector is not None:
        raise typer.BadParameter("--sector applies to --customers")


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
    customer_items: CustomersOption = False,
    sector: Annotated[
        str | None, typer.Option(help="--customers: the sector to use instead of the proposed one.")
    ] = None,
    tax_items: TaxOption = False,
    tax_class: TaxClassOption = None,
    related_items: RelatedOption = False,
    netdebt_items: NetdebtOption = False,
) -> None:
    """Confirm items, all or nothing: the label joins the dictionary and the company's matching
    facts are confirmed. The item must have a proposed account, or pass --account. A proposed new
    account is added to the dictionary first (--name and --parent change it; --account uses an
    existing account instead). Held documents and the documents whose facts changed are then
    checked again (a fix that clears a held document's findings loads it; a loaded document that
    now fails a check is held). With --ledgers the ids are ledger ids: their proposed accounts
    (or --account) are confirmed; only the user does this. With --customers the ids are customer
    ids: their proposed sectors (or --sector) are confirmed; only the user does this. With --tax the
    ids are tax ledger ids: their proposed classes (or --tax-class) are confirmed. With --related
    the ids are related party link ids; only the user does this. With --netdebt, net debt class
    ids; only the user confirms them."""
    kinds = (ledger_items, match_items, customer_items, tax_items, related_items)
    if not any(kinds) and not netdebt_items:  # mapping items: the decision service
        _check_unused(tax_class=tax_class, sector=sector)
        options = {"account": account, "scope": scope, "name": name, "parent": parent}
        actor = from_environment()
        show(
            decisions.confirm_mappings(data_dir(), company, ids, via="cli", actor=actor, **options)
        )
        return

    def body(run: Run) -> None:
        picked = (account, sector, tax_class, name, parent, scope != "company")
        if netdebt_items:
            review_netdebt.decide(run, company, ids, "confirmed", any(kinds + picked))
        elif related_items:
            _decide_related(run, company, ids, "confirmed")
        elif tax_items:
            others = any((ledger_items, match_items, customer_items, account, sector, name, parent))
            _confirm_tax(run, company, ids, tax_class, others)
        elif match_items:
            _confirm_matches(run, company, ids)
        elif customer_items:
            _check_unused(tax_class=tax_class)
            others = bool(ledger_items or account or scope != "company" or name or parent)
            _confirm_customers(run, company, ids, sector, others)
        else:
            _check_unused(tax_class=tax_class, sector=sector)
            _confirm_ledgers(run, company, ids, account, bool(scope != "company" or name or parent))

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
            "customers": customer_items,
            "sector": sector,
            "tax": tax_items,
            "tax_class": tax_class,
            "related": related_items,
            "netdebt": netdebt_items,
        },
        body,
    )


def _reject_matches(run: Run, company: str, ids: list[int]) -> None:
    for i in bank_proposals.decide(run, company, ids, "rejected"):
        typer.echo(f"match #{i} rejected")


def _reject_customers(run: Run, company: str, ids: list[int]) -> None:
    for crow in customers.reject(run, company, ids, actor=from_environment()):
        typer.echo(f"customer #{crow['customer_id']} {crow['customer']} rejected")


def _reject_tax(run: Run, company: str, ids: list[int], other_options: bool) -> None:
    if other_options:
        raise typer.BadParameter("--tax does not combine with --ledgers, --matches or --customers")
    for row in tax_ledgers.reject(run, company, ids, actor=from_environment()):
        typer.echo(f"tax ledger #{row['tax_ledger_id']} {row['ledger']!r} rejected")


def _reject_ledgers(run: Run, company: str, ids: list[int]) -> None:
    for row in ledgers.reject(run, company, ids, actor=from_environment()):
        typer.echo(f"ledger #{row['ledger_id']} {row['ledger']!r} rejected")


@review_app.command("reject")
def review_reject(
    company: CompanyArg,
    ids: Annotated[list[int], typer.Argument(help="Item ids from `fin review list`.")],
    ledger_items: LedgersOption = False,
    match_items: MatchesOption = False,
    customer_items: CustomersOption = False,
    tax_items: TaxOption = False,
    related_items: RelatedOption = False,
    netdebt_items: NetdebtOption = False,
) -> None:
    """Reject items, all or nothing: their facts stay unconfirmed. With --ledgers the ids are
    ledger ids: the ledgers stay unmapped. With --customers the ids are customer ids: the
    customers stay unmapped. With --matches they are match proposal ids. With --tax they are tax
    ledger ids: the ledgers have no tax class. With --related, related party link ids. With
    --netdebt, net debt class ids: the ledger keeps its default class."""
    flags = (ledger_items, match_items, customer_items, tax_items, related_items)
    if not any(flags) and not netdebt_items:  # mapping items: the decision service
        actor = from_environment()
        show(decisions.reject_mappings(data_dir(), company, ids, via="cli", actor=actor))
        return

    def body(run: Run) -> None:
        if netdebt_items:
            review_netdebt.decide(run, company, ids, "rejected", any(flags))
        elif related_items:
            _decide_related(run, company, ids, "rejected")
        elif tax_items:
            _reject_tax(run, company, ids, any((ledger_items, match_items, customer_items)))
        elif match_items:
            _reject_matches(run, company, ids)
        elif customer_items:
            _reject_customers(run, company, ids)
        else:
            _reject_ledgers(run, company, ids)

    args = {
        "ids": ids,
        "company": company,
        "ledgers": ledger_items,
        "matches": match_items,
        "customers": customer_items,
        "tax": tax_items,
        "related": related_items,
        "netdebt": netdebt_items,
    }
    execute("review reject", args, body)


def _echo_customers(rows: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        text = "\n".join(map(customers.format_customer, rows))
        typer.echo(text if rows else "no customers waiting")


def _echo_tax(rows: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(tax_ledgers.format_row, rows)) or "no tax ledgers waiting")


def _ledger_companies(run: Run) -> list[str]:
    return sorted(db.stem for db in companies_dir(run.root).glob("*.duckdb"))


def _echo_ledgers(rows: list[dict[str, Any]], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
    else:
        typer.echo("\n".join(map(ledgers.format_ledger, rows)) if rows else "no ledgers waiting")
