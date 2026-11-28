"""`fin review list|confirm|reject`: mapping items (`fin.map.review`) and the other review kinds
(`fin.services.reviews`). Confirm and reject call the same services as the UI."""

import json
from collections.abc import Callable
from typing import Annotated, Any

import typer

from fin.actor import from_environment
from fin.audit.errors import DependencyMissing
from fin.audit.logger import Run, execute
from fin.db.conn import no_company_message
from fin.diligence import bank_proposals, customers, ledgers
from fin.diligence.customers import list_customers
from fin.diligence.ledgers import list_ledgers
from fin.disclosures import related
from fin.fdd import tax_ledgers
from fin.map import review, review_netdebt
from fin.map.commands import (
    CompanyArg,
    CustomersOption,
    LedgersOption,
    MatchesOption,
    NetdebtOption,
    OptionalCompany,
    RelatedOption,
    TaxClassOption,
    TaxOption,
    _placement,
)
from fin.paths import companies_dir, company_db_path, data_dir
from fin.services import decisions, reviews
from fin.services.result import show

review_app = typer.Typer(
    no_args_is_help=True, help="Review mapping proposals: list, confirm or reject them."
)
# When several kind flags are given, the first of these wins (as `review list` does).
KIND_ORDER = ("netdebt", "related", "tax", "matches", "customers", "ledgers")


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


def _check_unused(tax_class: str | None = None, sector: str | None = None) -> None:
    """Usage errors for options that belong to another kind of item."""
    if tax_class is not None:
        raise typer.BadParameter("--tax-class applies to --tax")
    if sector is not None:
        raise typer.BadParameter("--sector applies to --customers")


def _kind(flags: dict[str, bool]) -> str | None:
    """The review kind the flags name (the first in KIND_ORDER), or None for mapping items."""
    return next((kind for kind in KIND_ORDER if flags[kind]), None)


# The option a review kind takes instead of its proposal; the other kinds take none.
CHOICE_OPTION = {"ledgers": "account", "customers": "sector", "tax": "tax_class"}


def _confirm_choice(kind: str, flags: dict[str, bool], options: dict[str, Any]) -> str | None:
    """The value chosen instead of the proposal for `kind`. Refuses another review kind's flag and
    any option `kind` does not take (rule 15: no option is accepted and ignored)."""
    others = [f"--{k}" for k, on in flags.items() if on and k != kind]
    if others:
        raise typer.BadParameter(f"--{kind} does not combine with {', '.join(others)}")
    allowed = CHOICE_OPTION.get(kind)
    named = ("account", "sector", "tax_class", "name", "parent", "each")
    unused = [k for k in named if options[k] and k != allowed]
    if options["scope"] != "company":
        unused.append("scope")
    if unused:
        listed = ", ".join("--" + k.replace("_", "-") for k in unused)
        raise typer.BadParameter(f"{listed}: not an option of --{kind}")
    return options[allowed] if allowed else None


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
    each: Annotated[
        bool,
        typer.Option(
            "--each",
            help="Confirm every id on its own, low-confidence ones too (each one was looked at).",
        ),
    ] = False,
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
    facts are confirmed. A proposal with confidence below 0.8 is confirmed only when its id is
    named alone or with --each; named with others it is left out and listed.
    The item must have a proposed account, or pass --account. A proposed new
    account is added to the dictionary first (--name and --parent change it; --account uses an
    existing account instead). Held documents and the documents whose facts changed are then
    checked again (a fix that clears a held document's findings loads it; a loaded document that
    now fails a check is held). With --ledgers the ids are ledger ids: their proposed accounts
    (or --account) are confirmed; only the user does this. With --customers the ids are customer
    ids: their proposed sectors (or --sector) are confirmed; only the user does this. With --tax the
    ids are tax ledger ids: their proposed classes (or --tax-class) are confirmed. With --related
    the ids are related party link ids; only the user does this. With --netdebt, net debt class
    ids; only the user confirms them."""
    flags = {
        "netdebt": netdebt_items,
        "related": related_items,
        "tax": tax_items,
        "matches": match_items,
        "customers": customer_items,
        "ledgers": ledger_items,
    }
    options = {"account": account, "scope": scope, "name": name, "parent": parent, "each": each}
    kind = _kind(flags)
    if kind is None:  # mapping items
        _check_unused(tax_class=tax_class, sector=sector)
        show(
            decisions.confirm_mappings(
                data_dir(), company, ids, via="cli", actor=from_environment(), **options
            )
        )
        return
    choice = _confirm_choice(kind, flags, options | {"sector": sector, "tax_class": tax_class})
    show(
        reviews.confirm_items(
            data_dir(), company, ids, kind=kind, via="cli", actor=from_environment(), choice=choice
        )
    )


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
    flags = {
        "netdebt": netdebt_items,
        "related": related_items,
        "tax": tax_items,
        "matches": match_items,
        "customers": customer_items,
        "ledgers": ledger_items,
    }
    actor = from_environment()
    kind = _kind(flags)
    if kind is None:  # mapping items
        show(decisions.reject_mappings(data_dir(), company, ids, via="cli", actor=actor))
        return
    others = {k for k, on in flags.items() if on and k != kind}
    if kind == "netdebt" and others:
        raise typer.BadParameter("--netdebt takes no other review option")
    if kind == "tax" and others & {"ledgers", "matches", "customers"}:
        raise typer.BadParameter("--tax does not combine with --ledgers, --matches or --customers")
    show(reviews.reject_items(data_dir(), company, ids, kind=kind, via="cli", actor=actor))


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
