"""Checks of an account a mapping proposal names, or defines as new (see fin.map.review)."""

import re
from typing import Any

from fin.audit.errors import InvalidMapping
from fin.map.dictionary import Account, account_problem

ACCOUNT_NAME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")  # snake_case
NEW_ACCOUNT_FIELDS = ("parent", "sign", "unit", "description")


def check_account(accounts: dict[str, Account], account: str, item: dict[str, Any]) -> None:
    if account not in accounts:
        raise InvalidMapping(f"unknown account {account!r}")
    if accounts[account].statement != item["statement"]:
        raise InvalidMapping(
            f"account {account!r} is a {accounts[account].statement} account, but "
            f"{item['label']!r} is on the {item['statement']}"
        )


def new_account_row(
    accounts: dict[str, Account], name: str, definition: Any, statement: str
) -> dict[str, str]:
    """Check a new-account proposal for `statement` and return it as a schedule_iii.csv row.
    `definition` is {parent, sign, unit, description}; parent and sign are null for a top line."""
    if not isinstance(name, str) or not ACCOUNT_NAME.match(name):
        raise InvalidMapping(f"new account {name!r}: use a snake_case name, e.g. loans_to_nbfcs")
    if name in accounts:
        raise InvalidMapping(
            f"new account {name!r} is already in the dictionary; propose it as an existing account"
        )
    if not isinstance(definition, dict) or set(definition) != set(NEW_ACCOUNT_FIELDS):
        raise InvalidMapping(
            f"new account {name!r}: new_account must have exactly {', '.join(NEW_ACCOUNT_FIELDS)}"
        )
    parent, sign, unit, description = (definition[field] for field in NEW_ACCOUNT_FIELDS)
    if parent is not None and not isinstance(parent, str):
        raise InvalidMapping(f"new account {name!r}: parent must be an account name or null")
    if sign is not None and (isinstance(sign, bool) or sign not in (1, -1)):
        raise InvalidMapping(f"new account {name!r}: sign must be 1, -1 or null")
    if not isinstance(unit, str) or not isinstance(description, str) or not description.strip():
        raise InvalidMapping(f"new account {name!r}: unit and description must be text")
    row = {
        "account": name,
        "statement": statement,
        "parent": parent or "",
        "sign": "" if sign is None else str(sign),
        "unit": unit,
        "description": description.strip(),
    }
    problem = account_problem(row, {n: a.statement for n, a in accounts.items()})
    if problem:
        raise InvalidMapping(f"new account {name!r}: {problem}")
    return row
