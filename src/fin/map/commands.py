"""`fin map propose|set|accounts`, and the arguments `fin review` shares
(`fin.map.review_commands`). The logic is in `fin.map.review` and `fin.services.decisions`."""

import csv
import io
import json
from pathlib import Path
from typing import Annotated

import typer

from fin.actor import from_environment
from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run, execute
from fin.map import review
from fin.map.dictionary import added_rows
from fin.paths import COMPANY_NAME, data_dir
from fin.services import decisions
from fin.services.result import show

map_app = typer.Typer(
    no_args_is_help=True, help="Mapping proposals, company mappings and the account list."
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
    """Map a label to an account for this company only, and apply it to the facts already loaded:
    the documents they are in are checked again, so a finding the old mapping caused is resolved
    without a re-ingest (`fin findings list`). It wins over the shared dictionary for this company,
    for files ingested later too."""
    key = (statement, section, label)
    show(
        decisions.set_mapping(
            data_dir(), company, key, account, via="cli", actor=from_environment()
        )
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
