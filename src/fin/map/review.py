"""Mapping review (docs/product.md): from an unmapped label to a dictionary entry.

1. `write_pending` (called after ingest) lists the labels the dictionary did not map, with their
   facts, in `review/<company>/pending.json`. Each item has an id, a status and, once proposed, an
   account, confidence and reason:  pending -> proposed -> (confirmed: removed from the file) or
   rejected.
2. `propose` stores Claude's proposals in that file. Nothing is applied.
3. `confirm` (`fin.map.review_confirm`) adds the label to `label_map` (the dictionary grows) and
   sets `account`, `mapping_id` and `confirmed = true` on the company's matching facts. `reject`
   leaves the facts unconfirmed.

A proposal may name a new account (`new_account`: parent, sign, unit, description) when no
existing account fits. It is stored on the item like any proposal; only `confirm` adds the account
to the shared `accounts` table (source 'review'), in the same transaction as the label.

`set_mapping` (`fin map set`) maps a label straight to an account for one company, without a pending
item: the way to fix a wrong mapping behind a finding. `fin.map.review_confirm.remap_facts` then
applies it to the facts already loaded, so the documents are checked again without a re-ingest.
Unmapped labels of held documents are pending items like those of loaded ones.

Every step appends a row to the audit table `mapping_decisions`. The caller passes the actor
(`fin.actor`): 'user', or 'ai:claude' with on_request = true when Claude decided because the user
asked. Proposals are always 'ai:claude'.
"""

import json
from pathlib import Path
from typing import Any

from fin.actor import CLAUDE, recorded
from fin.audit.errors import ConflictingMapping, DataFolderNotWritable, InvalidMapping
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.map.dictionary import (
    Account,
    label_key,
    load_accounts,
    upsert_label,
)
from fin.map.normalize import normalize_label
from fin.map.review_accounts import check_account, new_account_row
from fin.paths import companies_dir, company_db_path, dictionary_db_path, review_dir

SCOPES = ("global", "company")
LOW_CONFIDENCE = 0.7  # proposals below this are logged as E302
OPEN = ("pending", "proposed")  # statuses still waiting for a decision

Pending = dict[str, Any]  # {"company", "next_id", "items": [...]}
Item = dict[str, Any]


def _pending_path(root: Path, company: str) -> Path | None:
    """`review/<company>/pending.json` for a loaded company, else None. The folder name comes from
    the company databases on disk, never from the input, so it cannot point anywhere else."""
    loaded = {db.stem: db.stem for db in companies_dir(root).glob("*.duckdb")}
    name = loaded.get(company)
    return None if name is None else review_dir(root, name) / "pending.json"


def read_pending(root: Path, company: str) -> Pending:
    path = _pending_path(root, company)
    if path is None or not path.is_file():
        return {"company": company, "next_id": 1, "items": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidMapping(f"cannot read {path}: {exc}") from exc


def save_pending(root: Path, company: str, pending: Pending) -> None:
    path = _pending_path(root, company)
    if path is None:
        raise InvalidMapping(f"{company!r} is not a loaded company")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(pending, file, indent=2)
            file.write("\n")
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc


def item_key(item: Item) -> tuple[str, str, str]:
    return label_key(item["statement"], item["section"], item["label"])


def write_pending(run: Run, company: str) -> int:
    """Rebuild `review/<company>/pending.json` from the company's unmapped facts, keeping the ids,
    proposals and rejections already in it. Returns the number of open items. Writes nothing when
    there are no unmapped labels and no file."""
    with connect(company_db_path(run.root, company)) as con:
        rows = con.execute(
            "SELECT r.statement, r.section, r.raw_label, d.file_name, "
            "coalesce(r.sheet || '!' || r.cell, 'page ' || CAST(r.page AS VARCHAR)), "
            "coalesce(f.period || ': ', '') || r.raw_value_text "
            "FROM facts f JOIN raw_values r USING (raw_id) JOIN documents d ON d.doc_id = f.doc_id "
            "WHERE f.account IS NULL AND d.status IN ('loaded', 'held') ORDER BY f.raw_id"
        ).fetchall()
    found: dict[tuple[str, str, str], Item] = {}
    for statement, section, label, file_name, where, sample in rows:
        item = found.setdefault(
            label_key(statement, section, label),
            {"statement": statement, "section": section, "label": label, "facts": 0, "where": []},
        )
        item.setdefault("sample", sample)  # the first value, as printed: what the label holds
        item["facts"] += 1
        if len(item["where"]) < 3:
            item["where"].append(f"{file_name} {where}")
    pending = read_pending(run.root, company)
    if not found and not pending["items"]:
        return 0
    old = {item_key(item): item for item in pending["items"]}
    items = []
    for key, item in found.items():
        if key in old:  # keep id, status and proposal
            item = {**old[key], **{k: item.get(k) for k in ("facts", "where", "sample")}}
        else:
            item = {"id": pending["next_id"], "status": "pending", **item}
            item.update(account=None, confidence=None, reason=None)
            pending["next_id"] += 1
        items.append(item)
    pending["items"] = sorted(items, key=lambda i: i["id"])
    save_pending(run.root, company, pending)
    open_items = sum(1 for i in items if i["status"] in OPEN)
    run.event(
        "review",
        f"{open_items} labels waiting for review in review/{company}/pending.json",
        context={"company": company, "open": open_items},
    )
    return open_items


def list_accounts(root: Path, statement: str | None = None) -> list[tuple[str, ...]]:
    """(account, statement, parent, sign, unit, description, source) for every account (or one
    statement's), sorted by statement and account. source is 'seed' or 'review' (user-added)."""
    return [
        (*account.row(name), account.source)
        for name, account in load_accounts(root).items()
        if statement in (None, account.statement)
    ]


def set_mapping(
    run: Run,
    company: str,
    statement: str,
    label: str,
    account: str,
    section: str = "",
    *,
    actor: str,
) -> str:
    """Map a label (as written) to an account for this company only: add a company-scope row to
    `label_map`, or change the account of the one already there. It wins over global mappings
    (`fin.map.dictionary.find`); `review_confirm.remap_facts` applies it to loaded facts.
    Returns 'added', 'changed' or 'unchanged'."""
    item = {
        "statement": statement,
        "section": section,
        "label": label,
        "confidence": None,
        "reason": None,
    }
    check_account(load_accounts(run.root), account, item)
    key = label_key(statement, section, label)
    if not key[2]:
        raise InvalidMapping(f"label {label!r} is empty once normalized")
    with connect(dictionary_db_path(run.root), write=True) as con:
        _, outcome = upsert_label(con, key, account, company, "map set", run.run_id, "replace")
    if outcome != "unchanged":
        decision = "edited" if outcome == "changed" else "confirmed"
        decide(run, company, item, decision, account, "company", actor)
    return outcome


def decide(
    run: Run,
    company: str,
    item: Item,
    decision: str,
    account: str | None,
    scope: str | None,
    actor: str,
) -> None:
    """Append a mapping_decisions row. Proposals are always the AI's (`fin.actor.recorded`)."""
    actor, on_request = recorded(actor, decision)
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


def find_item(pending: Pending, item_id: int) -> Item:
    for item in pending["items"]:
        if item["id"] == item_id:
            return item
    raise InvalidMapping(f"no item with id {item_id} for {pending['company']}")


def _check_fields(number: int, proposal: Any) -> None:
    """The proposal has its keys, with text where text is expected and confidence in 0-1 (E304)."""
    if not isinstance(proposal, dict):
        raise InvalidMapping(f"proposal {number}: expected an object")
    for field in ("label", "account", "confidence", "reason"):
        if field not in proposal:
            raise InvalidMapping(f"proposal {number}: missing {field!r}")
    for field in ("label", "account", "reason", "statement", "section"):
        if field in proposal and not isinstance(proposal[field], str):
            raise InvalidMapping(f"proposal {number}: {field!r} must be text")
    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise InvalidMapping(f"proposal {number}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise InvalidMapping(f"proposal {number}: confidence must be from 0 to 1")


def _check_proposal(
    number: int,
    proposal: Any,
    pending: Pending,
    accounts: dict[str, Account],
    new_rows: dict[str, dict[str, str]],
) -> Item:
    """Check proposal `number` and return the pending item it is for (E304 when it is bad). A new
    account it defines is added to `new_rows`."""
    company = pending["company"]
    _check_fields(number, proposal)
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
            else f"proposal {number}: {proposal['label']!r} is not in review/{company}/pending.json"
        )
    item = matches[0]
    if "new_account" in proposal:
        row = new_account_row(
            accounts, proposal["account"], proposal["new_account"], item["statement"]
        )
        if new_rows.setdefault(row["account"], row) != row:
            raise InvalidMapping(
                f"proposal {number}: new account {row['account']!r} is defined twice, differently"
            )
    else:
        check_account(accounts, proposal["account"], item)
    return item


def _stored_new_account(new: dict[str, str] | None) -> dict[str, Any] | None:
    """A proposed new account as kept on its pending item (sign as +1/-1, no parent as None)."""
    if new is None:
        return None
    return {
        "parent": new["parent"] or None,
        "sign": int(new["sign"]) if new["sign"] else None,
        "unit": new["unit"],
        "description": new["description"],
    }


def propose(run: Run, company: str, proposals: list[dict[str, Any]]) -> int:
    """Store proposals on the matching pending items. All or nothing: any bad proposal (E304) or
    two different accounts for one label (E303) applies none. A proposal with `new_account`
    names an account that is not in the dictionary yet (see `new_account_row`)."""
    pending = read_pending(run.root, company)
    accounts = load_accounts(run.root)
    chosen: dict[int, tuple[Item, dict[str, Any]]] = {}
    new_rows: dict[str, dict[str, str]] = {}  # new accounts proposed, by name
    for number, proposal in enumerate(proposals, start=1):
        item = _check_proposal(number, proposal, pending, accounts, new_rows)
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
            new_account=_stored_new_account(new),
        )
        decide(run, company, item, "proposed", item["account"], None, CLAUDE)
        if item["confidence"] < LOW_CONFIDENCE:
            run.event(
                "map",
                f"{item['label']!r} proposed as {item['account']} with confidence "
                f"{item['confidence']}: low, review it",
                level="warning",
                code="E302",
                context={"company": company, "id": item["id"]},
            )
    save_pending(run.root, company, pending)
    return len(chosen)


def reject(run: Run, company: str, item_ids: list[int], *, actor: str) -> None:
    """Mark the items rejected; all or nothing (E304 for an unknown id rejects none). Their facts
    stay unconfirmed and they leave the open list."""
    pending = read_pending(run.root, company)
    items = [find_item(pending, item_id) for item_id in dict.fromkeys(item_ids)]
    for item in items:
        item["status"] = "rejected"
        decide(run, company, item, "rejected", item["account"], None, actor)
    save_pending(run.root, company, pending)


def open_items(pending: Pending) -> list[Item]:
    return [item for item in pending["items"] if item["status"] in OPEN]


def companies_with_pending(root: Path) -> list[str]:
    return sorted(p.parent.name for p in (root / "review").glob("*/pending.json"))
