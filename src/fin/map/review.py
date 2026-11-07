"""Mapping review (PLAN.md Phase 5): from an unmapped label to a dictionary entry.

1. `write_pending` (called after ingest) lists the labels the dictionary did not map, with their
   facts, in `review/<company>/pending.json`. Each item has an id, a status and, once proposed, an
   account, confidence and reason:  pending -> proposed -> (confirmed: removed from the file) or
   rejected.
2. `propose` stores Claude's proposals in that file. Nothing is applied.
3. `confirm` adds the label to `label_map` (the dictionary grows) and sets `account`, `mapping_id`
   and `confirmed = true` on the company's matching facts. `reject` leaves the facts unconfirmed.

A proposal may name a new account (`new_account`: parent, sign, unit, description) when no
existing account fits. It is stored on the item like any proposal; only `confirm` adds the account
to the shared `accounts` table (source 'review'), in the same transaction as the label.

`set_mapping` (`fin map set`) maps a label straight to an account for one company, without a pending
item: the way to fix the mapping behind a document that a validation gate rejected (it never
loaded, so it has no pending items), before ingesting it again.

Every step appends a row to the audit table `mapping_decisions`. The actor is `claude` (with
on_request = true) when the `CLAUDECODE` environment variable is set, else `human`.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import ConflictingMapping, DataFolderNotWritable, InvalidMapping
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.map.dictionary import account_problem
from fin.map.normalize import normalize_label
from fin.paths import company_db_path, dictionary_db_path, review_dir

SCOPES = ("global", "company")
LOW_CONFIDENCE = 0.7  # proposals below this are logged as E302
OPEN = ("pending", "proposed")  # statuses still waiting for a decision
ACCOUNT_NAME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")  # snake_case
NEW_ACCOUNT_FIELDS = ("parent", "sign", "unit", "description")

Pending = dict[str, Any]  # {"company", "next_id", "items": [...]}
Item = dict[str, Any]


def current_actor() -> tuple[str, bool]:
    """(actor, on_request) for a mapping decision made by a command run now."""
    if os.environ.get("CLAUDECODE"):
        return "claude", True
    return "human", False


def pending_path(root: Path, company: str) -> Path:
    return review_dir(root, company) / "pending.json"


def read_pending(root: Path, company: str) -> Pending:
    path = pending_path(root, company)
    if not path.is_file():
        return {"company": company, "next_id": 1, "items": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidMapping(f"cannot read {path}: {exc}") from exc


def _save(root: Path, company: str, pending: Pending) -> None:
    path = pending_path(root, company)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pending, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc


def _key(statement: str, section: str, label: str) -> tuple[str, str, str]:
    return statement, normalize_label(section), normalize_label(label)


def _item_key(item: Item) -> tuple[str, str, str]:
    return _key(item["statement"], item["section"], item["label"])


def write_pending(run: Run, company: str) -> int:
    """Rebuild `review/<company>/pending.json` from the company's unmapped facts, keeping the ids,
    proposals and rejections already in it. Returns the number of open items. Writes nothing when
    there are no unmapped labels and no file."""
    with connect(company_db_path(run.root, company)) as con:
        rows = con.execute(
            "SELECT r.statement, r.section, r.raw_label, d.file_name, "
            "coalesce(r.sheet || '!' || r.cell, 'page ' || CAST(r.page AS VARCHAR)) "
            "FROM facts f JOIN raw_values r USING (raw_id) JOIN documents d ON d.doc_id = f.doc_id "
            "WHERE f.account IS NULL AND d.status = 'loaded' ORDER BY f.raw_id"
        ).fetchall()
    found: dict[tuple[str, str, str], Item] = {}
    for statement, section, label, file_name, where in rows:
        item = found.setdefault(
            _key(statement, section, label),
            {"statement": statement, "section": section, "label": label, "facts": 0, "where": []},
        )
        item["facts"] += 1
        if len(item["where"]) < 3:
            item["where"].append(f"{file_name} {where}")
    pending = read_pending(run.root, company)
    if not found and not pending["items"]:
        return 0
    old = {_item_key(item): item for item in pending["items"]}
    items = []
    for key, item in found.items():
        if key in old:  # keep id, status and proposal
            item = {**old[key], "facts": item["facts"], "where": item["where"]}
        else:
            item = {"id": pending["next_id"], "status": "pending", **item}
            item.update(account=None, confidence=None, reason=None)
            pending["next_id"] += 1
        items.append(item)
    pending["items"] = sorted(items, key=lambda i: i["id"])
    _save(run.root, company, pending)
    open_items = sum(1 for i in items if i["status"] in OPEN)
    run.event(
        "review",
        f"{open_items} labels waiting for review in review/{company}/pending.json",
        context={"company": company, "open": open_items},
    )
    return open_items


def _accounts(root: Path) -> dict[str, str]:
    """{account: statement} from the dictionary."""
    with connect(dictionary_db_path(root)) as con:
        return dict(con.execute("SELECT account, statement FROM accounts").fetchall())


def _per_share(root: Path, account: str) -> bool:
    with connect(dictionary_db_path(root)) as con:
        row = con.execute("SELECT unit FROM accounts WHERE account = ?", [account]).fetchone()
    return row is not None and row[0] == "rupees_per_share"


def list_accounts(root: Path, statement: str | None = None) -> list[tuple[str, ...]]:
    """(account, statement, parent, sign, unit, description, source) for every account (or one
    statement's), sorted by statement and account. source is 'seed' or 'review' (user-added)."""
    with connect(dictionary_db_path(root)) as con:
        return con.execute(
            "SELECT account, statement, coalesce(parent, ''), coalesce(CAST(sign AS VARCHAR), ''), "
            "unit, description, source FROM accounts WHERE ? IS NULL OR statement = ? "
            "ORDER BY statement, account",
            [statement, statement],
        ).fetchall()


def set_mapping(
    run: Run, company: str, statement: str, label: str, account: str, section: str = ""
) -> str:
    """Map a label (as written) to an account for this company only: add a company-scope row to
    `label_map`, or change the account of the one already there. It wins over global mappings
    for documents ingested from now on (`fin.map.dictionary.find`); loaded facts do not change.
    Returns 'added', 'changed' or 'unchanged'."""
    item = {"statement": statement, "section": section, "label": label}
    _check_account(_accounts(run.root), account, item)
    statement, section_key, label_key = _key(statement, section, label)
    if not label_key:
        raise InvalidMapping(f"label {label!r} is empty once normalized")
    with connect(dictionary_db_path(run.root), write=True) as con:
        existing = con.execute(
            "SELECT mapping_id, account FROM label_map WHERE scope = 'company' AND company = ? "
            "AND statement = ? AND section = ? AND label = ?",
            [company, statement, section_key, label_key],
        ).fetchone()
        if existing is not None and existing[1] == account:
            return "unchanged"
        if existing is not None:
            con.execute(
                "UPDATE label_map SET account = ?, source = 'map set', run_id = ? "
                "WHERE mapping_id = ?",
                [account, run.run_id, existing[0]],
            )
        else:
            con.execute(
                "INSERT INTO label_map (label, statement, section, account, scope, company, "
                "source, created_at, run_id) VALUES (?, ?, ?, ?, 'company', ?, 'map set', ?, ?)",
                [
                    label_key,
                    statement,
                    section_key,
                    account,
                    company,
                    datetime.now(UTC).replace(tzinfo=None),
                    run.run_id,
                ],
            )
    _decide(
        run,
        company,
        {**item, "confidence": None, "reason": None},
        "edited" if existing else "confirmed",
        account,
        "company",
    )
    return "changed" if existing else "added"


def _check_account(accounts: dict[str, str], account: str, item: Item) -> None:
    if account not in accounts:
        raise InvalidMapping(f"unknown account {account!r}")
    if accounts[account] != item["statement"]:
        raise InvalidMapping(
            f"account {account!r} is a {accounts[account]} account, but {item['label']!r} "
            f"is on the {item['statement']}"
        )


def _new_account(
    accounts: dict[str, str], name: str, definition: Any, statement: str
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
    problem = account_problem(row, accounts)
    if problem:
        raise InvalidMapping(f"new account {name!r}: {problem}")
    return row


def _decide(
    run: Run, company: str, item: Item, decision: str, account: str | None, scope: str | None
) -> None:
    """Append a mapping_decisions row. Proposals are always Claude's."""
    actor, on_request = ("claude", False) if decision == "proposed" else current_actor()
    run.record(
        "mapping_decisions",
        {
            "company": company,
            "statement": item["statement"],
            "section": item["section"],
            "raw_label": item["label"],
            "account": account,
            "scope": scope,
            "decision": decision,
            "actor": actor,
            "on_request": on_request,
            "confidence": item["confidence"],
            "reason": item["reason"],
        },
    )


def _find_item(pending: Pending, item_id: int) -> Item:
    for item in pending["items"]:
        if item["id"] == item_id:
            return item
    raise InvalidMapping(f"no item with id {item_id} for {pending['company']}")


def propose(run: Run, company: str, proposals: list[dict[str, Any]]) -> int:
    """Store proposals on the matching pending items. All or nothing: any bad proposal (E304) or
    two different accounts for one label (E303) applies none. A proposal with `new_account`
    names an account that is not in the dictionary yet (see `_new_account`)."""
    pending = read_pending(run.root, company)
    accounts = _accounts(run.root)
    chosen: dict[int, tuple[Item, dict[str, Any]]] = {}
    new_rows: dict[str, dict[str, str]] = {}  # new accounts proposed, by name
    for number, proposal in enumerate(proposals, start=1):
        if not isinstance(proposal, dict):
            raise InvalidMapping(f"proposal {number}: expected an object")
        for field in ("label", "account", "confidence", "reason"):
            if field not in proposal:
                raise InvalidMapping(f"proposal {number}: missing {field!r}")
        confidence = proposal["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise InvalidMapping(f"proposal {number}: confidence must be a number from 0 to 1")
        if not 0 <= confidence <= 1:
            raise InvalidMapping(f"proposal {number}: confidence must be from 0 to 1")
        matches = [
            item
            for item in pending["items"]
            if normalize_label(item["label"]) == normalize_label(proposal["label"])
            and proposal.get("statement", item["statement"]) == item["statement"]
            and normalize_label(proposal.get("section", item["section"]))
            == normalize_label(item["section"])
        ]
        if len(matches) != 1:
            raise InvalidMapping(
                f"proposal {number}: {proposal['label']!r} matches {len(matches)} items in "
                f"review/{company}/pending.json; give statement and section to pick one"
                if matches
                else f"proposal {number}: {proposal['label']!r} is not in "
                f"review/{company}/pending.json"
            )
        item = matches[0]
        if "new_account" in proposal:
            row = _new_account(
                accounts, proposal["account"], proposal["new_account"], item["statement"]
            )
            if new_rows.setdefault(row["account"], row) != row:
                raise InvalidMapping(
                    f"proposal {number}: new account {row['account']!r} is defined twice, "
                    "differently"
                )
        else:
            _check_account(accounts, proposal["account"], item)
        if item["id"] in chosen and chosen[item["id"]][1]["account"] != proposal["account"]:
            raise ConflictingMapping(
                f"{proposal['label']!r} is proposed as both {chosen[item['id']][1]['account']} "
                f"and {proposal['account']}"
            )
        chosen[item["id"]] = (item, proposal)
    for item, proposal in chosen.values():
        new = new_rows.get(proposal["account"]) if "new_account" in proposal else None
        item.update(
            status="proposed",
            account=proposal["account"],
            confidence=proposal["confidence"],
            reason=proposal["reason"],
            new_account=None
            if new is None
            else {
                "parent": new["parent"] or None,
                "sign": int(new["sign"]) if new["sign"] else None,
                "unit": new["unit"],
                "description": new["description"],
            },
        )
        _decide(run, company, item, "proposed", item["account"], None)
        if item["confidence"] < LOW_CONFIDENCE:
            run.event(
                "map",
                f"{item['label']!r} proposed as {item['account']} with confidence "
                f"{item['confidence']}: low, review it",
                level="warning",
                code="E302",
                context={"company": company, "id": item["id"]},
            )
    _save(run.root, company, pending)
    return len(chosen)


def _account_to_add(
    accounts: dict[str, str], item: Item, name: str | None, parent: str | None
) -> dict[str, str] | None:
    """The new account to add when confirming `item` (its proposal, with `name` and `parent`
    when given), or None when it was already added by confirming another item the same way."""
    proposed = item["new_account"]
    name = name or item["account"]
    definition = {**proposed, "parent": parent or proposed["parent"]}
    if name in accounts and name == item["account"] and parent is None:
        return None  # another item with the same new account was confirmed first
    return _new_account(accounts, name, definition, item["statement"])


def confirm(
    run: Run,
    company: str,
    item_id: int,
    account: str | None = None,
    scope: str = "company",
    name: str | None = None,
    parent: str | None = None,
) -> int:
    """Add the label to the dictionary and confirm the company's matching facts. `account`
    overrides the proposed one with an existing account. For a new-account proposal, the account
    is added first (`name` and `parent` override the proposed ones), in the same transaction as
    the label. Returns the number of facts confirmed."""
    pending = read_pending(run.root, company)
    item = _find_item(pending, item_id)
    if scope not in SCOPES:
        raise InvalidMapping(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}")
    accounts = _accounts(run.root)
    new = None
    if item.get("new_account") and account in (None, item["account"]):
        new = _account_to_add(accounts, item, name, parent)
        account = new["account"] if new else item["account"]
        if new is None:
            _check_account(accounts, account, item)
    else:
        if name is not None or parent is not None:
            raise InvalidMapping(
                f"item {item_id}: --name and --parent apply only to a new-account proposal"
            )
        account = account or item["account"]
        if account is None:
            raise InvalidMapping(f"item {item_id} has no proposed account; give one with --account")
        _check_account(accounts, account, item)
    statement, section, label = _item_key(item)
    owner = company if scope == "company" else None
    now = datetime.now(UTC).replace(tzinfo=None)
    with connect(dictionary_db_path(run.root), write=True) as con:
        existing = con.execute(
            "SELECT mapping_id, account FROM label_map WHERE label = ? AND statement = ? "
            "AND section = ? AND scope = ? AND company IS NOT DISTINCT FROM ?",
            [label, statement, section, scope, owner],
        ).fetchone()
        if existing is not None and existing[1] != account:
            raise ConflictingMapping(
                f"{item['label']!r} is already mapped to {existing[1]} ({scope} scope); "
                f"not changed to {account}"
            )
        con.execute("BEGIN TRANSACTION")
        try:
            if new is not None:
                con.execute(
                    "INSERT INTO accounts (account, statement, parent, sign, unit, description, "
                    "source, run_id) VALUES (?, ?, ?, ?, ?, ?, 'review', ?)",
                    [
                        new["account"],
                        new["statement"],
                        new["parent"] or None,
                        int(new["sign"]) if new["sign"] else None,
                        new["unit"],
                        new["description"],
                        run.run_id,
                    ],
                )
            if existing is not None:
                mapping_id = existing[0]
            else:
                row = con.execute(
                    "INSERT INTO label_map (label, statement, section, account, scope, company, "
                    "source, created_at, run_id) VALUES (?, ?, ?, ?, ?, ?, 'review', ?, ?) "
                    "RETURNING mapping_id",
                    [label, statement, section, account, scope, owner, now, run.run_id],
                ).fetchone()
                assert row is not None
                mapping_id = row[0]
            con.execute("COMMIT")
        except duckdb.Error:
            con.execute("ROLLBACK")
            raise
    if new is not None:
        where = f"under {new['parent']}" if new["parent"] else "as a top line"
        run.event(
            "review",
            f"account {account} added to the dictionary ({new['statement']}, {where})",
            context={"company": company, "id": item_id, "account": account},
        )
    with connect(company_db_path(run.root, company), write=True) as con:
        unmapped = con.execute(
            "SELECT f.raw_id, r.statement, r.section, r.raw_label FROM facts f "
            "JOIN raw_values r USING (raw_id) WHERE f.account IS NULL"
        ).fetchall()
        raw_ids = [
            raw_id for raw_id, *fields in unmapped if _key(*fields) == (statement, section, label)
        ]
        # An unmapped fact was stored in rupees; a per-share figure (EPS) is kept as printed.
        con.execute(
            "UPDATE facts SET account = ?, mapping_id = ?, confirmed = true, value = CASE WHEN ? "
            "THEN round(value / (SELECT r.rupees_per_unit FROM raw_values r "
            "WHERE r.raw_id = facts.raw_id), 2) ELSE value END "
            "WHERE raw_id IN (SELECT unnest(?))",
            [account, mapping_id, _per_share(run.root, account), raw_ids],
        )
    decision = "confirmed" if account == item["account"] and parent is None else "edited"
    _decide(run, company, item, decision, account, scope)
    pending["items"].remove(item)
    _save(run.root, company, pending)
    return len(raw_ids)


def reject(run: Run, company: str, item_id: int) -> None:
    """Mark the item rejected. Its facts stay unconfirmed and it leaves the open list."""
    pending = read_pending(run.root, company)
    item = _find_item(pending, item_id)
    item["status"] = "rejected"
    _decide(run, company, item, "rejected", item["account"], None)
    _save(run.root, company, pending)


def open_items(pending: Pending) -> list[Item]:
    return [item for item in pending["items"] if item["status"] in OPEN]


def companies_with_pending(root: Path) -> list[str]:
    return sorted(p.parent.name for p in (root / "review").glob("*/pending.json"))
