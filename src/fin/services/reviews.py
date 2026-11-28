"""The review kinds other than mapping items: Tally ledgers, customer sectors, tax ledger classes,
related party links, net debt classes and bank match proposals. Each is listed, confirmed and
rejected the same way from `fin review list|confirm|reject <co> --<kind>` and from the Mappings
screen; only the user decides (`actor`, `fin.actor`). `choice` is the value the user picks
instead of the proposal (an account, a sector or a tax class) for the kinds that have one."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run
from fin.diligence import bank_proposals, customers, ledgers, netdebt
from fin.disclosures import related
from fin.fdd import tax_ledgers
from fin.services.result import ActionResult, perform

Rows = list[dict[str, Any]]


def _ledgers(run: Run, company: str, ids: list[int], choice: str | None, actor: str) -> list[str]:
    rows = ledgers.confirm(run, company, ids, choice, actor=actor)
    return [f"ledger #{r['ledger_id']} {r['ledger']!r} confirmed as {r['account']}" for r in rows]


def _customers(run: Run, company: str, ids: list[int], choice: str | None, actor: str) -> list[str]:
    rows = customers.confirm(run, company, ids, choice, actor=actor)
    return [
        f"customer #{r['customer_id']} {r['customer']} confirmed as {r['sector']}" for r in rows
    ]


def _tax(run: Run, company: str, ids: list[int], choice: str | None, actor: str) -> list[str]:
    rows = tax_ledgers.confirm(run, company, ids, choice, actor=actor)
    return [
        f"tax ledger #{r['tax_ledger_id']} {r['ledger']!r} confirmed as {r['tax_class']}"
        for r in rows
    ]


def _related(run: Run, company: str, ids: list[int], status: str, actor: str) -> list[str]:
    return [
        f"link #{r['link_id']} {r['party']} -> {r['link_kind']} {r['target']} {status}"
        for r in related.decide(run, company, ids, status, actor=actor)
    ]


def _matches(run: Run, company: str, ids: list[int], status: str) -> list[str]:
    done = bank_proposals.decide(run, company, ids, status)
    after = "; applied by the next bank_vs_invoices run" if status == "confirmed" else ""
    return [f"match #{i} {status}{after}" for i in done]


def _netdebt(run: Run, company: str, ids: list[int], status: str) -> list[str]:
    return [netdebt.format_class(r) for r in netdebt.decide(run, company, ids, status)]


def _rejected(rows: Rows, key: str, name: str, what: str) -> list[str]:
    return [f"{what} #{r[key]} {r[name]!r} rejected" for r in rows]


@dataclass(frozen=True)
class Kind:
    title: str  # the heading on the Mappings screen
    id_key: str  # the id column of its rows
    waiting: Callable[[Run, str], Rows]  # the rows waiting for the user
    text: Callable[[dict[str, Any]], str]  # one row as the CLI prints it
    confirm: Callable[[Run, str, list[int], str | None, str], list[str]]
    reject: Callable[[Run, str, list[int], str], list[str]]
    choice: str | None = None  # what the user can pick instead of the proposal


KINDS: dict[str, Kind] = {
    "ledgers": Kind(
        "Tally ledgers",
        "ledger_id",
        lambda run, co: ledgers.list_ledgers(run, co, True),
        ledgers.format_ledger,
        _ledgers,
        lambda run, co, ids, actor: _rejected(
            ledgers.reject(run, co, ids, actor=actor), "ledger_id", "ledger", "ledger"
        ),
        "account",
    ),
    "customers": Kind(
        "Customer sectors",
        "customer_id",
        lambda run, co: customers.list_customers(run, co, True),
        customers.format_customer,
        _customers,
        lambda run, co, ids, actor: [
            f"customer #{r['customer_id']} {r['customer']} rejected"
            for r in customers.reject(run, co, ids, actor=actor)
        ],
        "sector",
    ),
    "tax": Kind(
        "Tax ledger classes",
        "tax_ledger_id",
        lambda run, co: tax_ledgers.list_rows(run, co, True),
        tax_ledgers.format_row,
        _tax,
        lambda run, co, ids, actor: _rejected(
            tax_ledgers.reject(run, co, ids, actor=actor), "tax_ledger_id", "ledger", "tax ledger"
        ),
        "tax class",
    ),
    "related": Kind(
        "Related party links",
        "link_id",
        lambda run, co: related.list_links(run, co, True),
        related.format_link,
        lambda run, co, ids, _choice, actor: _related(run, co, ids, "confirmed", actor),
        lambda run, co, ids, actor: _related(run, co, ids, "rejected", actor),
    ),
    "netdebt": Kind(
        "Net debt classes",
        "class_id",
        lambda run, co: netdebt.list_classes(run, co, True),
        netdebt.format_class,
        lambda run, co, ids, _choice, _actor: _netdebt(run, co, ids, "confirmed"),
        lambda run, co, ids, _actor: _netdebt(run, co, ids, "rejected"),
    ),
    "matches": Kind(
        "Bank matches",
        "proposal_id",
        lambda run, co: bank_proposals.list_proposals(run, co, True),
        bank_proposals.format_proposal,
        lambda run, co, ids, _choice, _actor: _matches(run, co, ids, "confirmed"),
        lambda run, co, ids, _actor: _matches(run, co, ids, "rejected"),
    ),
}


def confirm_items(
    root: Path,
    company: str,
    ids: list[int],
    *,
    kind: str,
    via: str,
    actor: str,
    choice: str | None = None,
) -> ActionResult:
    """`fin review confirm <co> <ids> --<kind>`: the proposals (or `choice` instead)."""
    spec = KINDS[kind]

    def body(run: Run) -> list[str]:
        if choice is not None and spec.choice is None:
            raise InvalidMapping(f"a {kind} item has nothing to choose; confirm or reject it")
        return spec.confirm(run, company, ids, choice, actor)

    args = {"company": company, "ids": ids, kind: True, "choice": choice}
    return perform(root, "review confirm", args | {"via": via, "actor": actor}, body)


def reject_items(
    root: Path, company: str, ids: list[int], *, kind: str, via: str, actor: str
) -> ActionResult:
    """`fin review reject <co> <ids> --<kind>`."""
    args = {"company": company, "ids": ids, kind: True, "via": via, "actor": actor}
    return perform(
        root, "review reject", args, lambda run: KINDS[kind].reject(run, company, ids, actor)
    )
