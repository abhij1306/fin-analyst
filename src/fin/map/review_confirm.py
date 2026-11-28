"""Confirm mapping items (`fin review confirm`) and apply a changed mapping to the facts already
loaded (`fin map set`, the UI's "change the mapping"). The pending file and the audit rows are
`fin.map.review`'s.

Low-confidence proposals (owner decision Q4, docs/specs/README.md): a proposal below
BULK_CONFIDENCE is confirmed on its own. A request that names several ids confirms the others and
leaves those out, naming them (warning E302); with `each` every id is confirmed in a step of its
own, as if named alone. The rule is here, not in the CLI, so the UI meets it too.
"""

from dataclasses import dataclass

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run
from fin.db.conn import connect, transaction
from fin.map.dictionary import (
    Account,
    copy_review_accounts,
    insert_account,
    label_key,
    load_accounts,
    load_label_map,
    upsert_label,
)
from fin.map.normalize import normalize_label
from fin.map.review import (
    SCOPES,
    Item,
    Pending,
    decide,
    find_item,
    item_key,
    read_pending,
    save_pending,
)
from fin.map.review_accounts import check_account, new_account_row
from fin.paths import company_db_path, dictionary_db_path

BULK_CONFIDENCE = 0.8  # Q4: a proposal below this is confirmed on its own
PER_SHARE = "rupees_per_share"


@dataclass(frozen=True)
class Confirmed:
    items: list[tuple[int, int]]  # (item id, facts confirmed) per confirmed item
    doc_ids: set[int]  # the documents whose facts changed
    skipped: list[Item]  # low-confidence items a bulk request left out


def low_confidence(item: Item) -> bool:
    return item["confidence"] is not None and item["confidence"] < BULK_CONFIDENCE


def _account_to_add(
    accounts: dict[str, Account], item: Item, name: str | None, parent: str | None
) -> dict[str, str] | None:
    """The new account to add when confirming `item` (its proposal, with `name` and `parent`
    when given), or None when it was already added by confirming another item the same way."""
    proposed = item["new_account"]
    chosen: str = name or item["account"]
    definition = {**proposed, "parent": parent or proposed["parent"]}
    if chosen in accounts and chosen == item["account"] and parent is None:
        return None  # another item with the same new account was confirmed first
    return new_account_row(accounts, chosen, definition, item["statement"])


def _resolve_account(
    accounts: dict[str, Account],
    item: Item,
    account: str | None,
    name: str | None,
    parent: str | None,
) -> tuple[str, dict[str, str] | None]:
    """(the account to confirm `item` as, the new account to add first or None)."""
    if item.get("new_account") and account in (None, item["account"]):
        new = _account_to_add(accounts, item, name, parent)
        if new is None:  # added by confirming another item with the same new account
            check_account(accounts, item["account"], item)
            return item["account"], None
        return new["account"], new
    if name is not None or parent is not None:
        raise InvalidMapping(
            f"item {item['id']}: --name and --parent apply only to a new-account proposal"
        )
    account = account or item["account"]
    if account is None:
        raise InvalidMapping(f"item {item['id']} has no proposed account; give one with --account")
    check_account(accounts, account, item)
    return account, None


def _write_mappings(
    run: Run, plan: list[tuple[Item, str, dict[str, str] | None]], company: str | None
) -> list[int]:
    """Add the new accounts and the label mappings in one transaction; the mapping_ids."""
    mapping_ids = []
    with connect(dictionary_db_path(run.root), write=True) as con, transaction(con):
        for item, account, new in plan:
            if new is not None:
                insert_account(con, new, "review", run.run_id)
            mapping_id, _ = upsert_label(
                con, item_key(item), account, company, "review", run.run_id, "refuse"
            )
            mapping_ids.append(mapping_id)
    return mapping_ids


def _undo_mappings(run: Run, mapping_ids: list[int], accounts: list[str]) -> None:
    """Remove the accounts and label mappings this batch added (`_write_mappings`), when confirming
    the facts failed after them, so a retry or a reject starts clean. Only this batch's rows: with
    `each`, the earlier batches of the same run stay confirmed."""
    with connect(dictionary_db_path(run.root), write=True) as con, transaction(con):
        con.execute(
            "DELETE FROM label_map WHERE run_id = ? AND list_contains(?, mapping_id)",
            [run.run_id, mapping_ids],
        )
        con.execute(
            "DELETE FROM accounts WHERE source = 'review' AND run_id = ? "
            "AND list_contains(?, account)",
            [run.run_id, accounts],
        )


def _confirm_facts(
    run: Run, company: str, confirmed: list[tuple[Item, str, int, bool]]
) -> tuple[list[int], set[int]]:
    """Set the account on the company's unmapped facts with each item's label, in one transaction.
    `confirmed` is (item, account, mapping_id, per-share). Returns how many facts per item, and the
    documents whose facts changed."""
    counts, doc_ids = [], set()
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        copy_review_accounts(run.root, con)
        unmapped = con.execute(
            "SELECT f.raw_id, r.statement, r.section, r.raw_label FROM facts f "
            "JOIN raw_values r USING (raw_id) WHERE f.account IS NULL"
        ).fetchall()
        for item, account, mapping_id, per_share in confirmed:
            raw_ids = [raw_id for raw_id, *key in unmapped if label_key(*key) == item_key(item)]
            # An unmapped fact was stored in rupees; a per-share figure (EPS) is kept as printed.
            changed = con.execute(
                "UPDATE facts SET account = ?, mapping_id = ?, confirmed = true, value = CASE "
                "WHEN ? THEN round(value / (SELECT r.rupees_per_unit FROM raw_values r "
                "WHERE r.raw_id = facts.raw_id), 2) ELSE value END "
                "WHERE raw_id IN (SELECT unnest(?)) RETURNING doc_id",
                [account, mapping_id, per_share, raw_ids],
            ).fetchall()
            counts.append(len(raw_ids))
            doc_ids.update(doc_id for (doc_id,) in changed)
    return counts, doc_ids


def _plan(
    run: Run,
    pending: Pending,
    item_ids: list[int],
    account: str | None,
    name: str | None,
    parent: str | None,
) -> list[tuple[Item, str, dict[str, str] | None]]:
    """Check every item of a confirm request before anything is written (E304 on the first that
    cannot be applied): (item, account, new account to add or None) per item, in order. A new
    account an earlier item adds counts as in the dictionary for the later ones."""
    accounts = load_accounts(run.root)
    plan = []
    for item_id in dict.fromkeys(item_ids):
        item = find_item(pending, item_id)
        resolved, new = _resolve_account(accounts, item, account, name, parent)
        if new is not None:
            sign = int(new["sign"]) if new["sign"] else None
            accounts[resolved] = Account(
                new["statement"], new["parent"] or None, sign, new["unit"], new["description"]
            )
        plan.append((item, resolved, new))
    return plan


def _confirm_batch(
    run: Run,
    company: str,
    item_ids: list[int],
    account: str | None,
    scope: str,
    name: str | None,
    parent: str | None,
    actor: str,
) -> tuple[list[tuple[int, int]], set[int]]:
    """Confirm `item_ids`, all or nothing: every item is checked first (E304), then the dictionary
    changes in one transaction (E303 rolls back every item) and the facts in another; when the
    facts fail, the dictionary rows this run added are removed again."""
    pending = read_pending(run.root, company)
    plan = _plan(run, pending, item_ids, account, name, parent)
    mapping_ids = _write_mappings(run, plan, company if scope == "company" else None)
    try:
        accounts = load_accounts(run.root)
        counts, doc_ids = _confirm_facts(
            run,
            company,
            [
                (item, resolved, mapping_id, accounts[resolved].unit == PER_SHARE)
                for (item, resolved, _), mapping_id in zip(plan, mapping_ids, strict=True)
            ],
        )
    except Exception:
        _undo_mappings(run, mapping_ids, [new["account"] for _, _, new in plan if new is not None])
        raise
    for item, resolved, new in plan:
        if new is not None:
            where = f"under {new['parent']}" if new["parent"] else "as a top line"
            run.event(
                "review",
                f"account {resolved} added to the dictionary ({new['statement']}, {where})",
                context={"company": company, "id": item["id"], "account": resolved},
            )
        decision = "confirmed" if resolved == item["account"] and parent is None else "edited"
        decide(run, company, item, decision, resolved, scope, actor)
        pending["items"].remove(item)
    save_pending(run.root, company, pending)
    return [(item["id"], count) for (item, _, _), count in zip(plan, counts, strict=True)], doc_ids


def _split_low(run: Run, company: str, ids: list[int], each: bool) -> tuple[list[int], list[Item]]:
    """(the ids to confirm, the low-confidence items left out). An unknown id is E304 here, before
    anything is written. E304 too when every id named is low and nothing would be confirmed."""
    pending = read_pending(run.root, company)
    items = [find_item(pending, item_id) for item_id in ids]
    if each or len(items) == 1:
        return ids, []
    skipped = [item for item in items if low_confidence(item)]
    if len(skipped) == len(items):
        listed = ", ".join(f"#{item['id']}" for item in skipped)
        raise InvalidMapping(
            f"{listed} have confidence below {BULK_CONFIDENCE}: confirm each on its own, or name "
            "them with --each; nothing confirmed"
        )
    return [item["id"] for item in items if not low_confidence(item)], skipped


def confirm(
    run: Run,
    company: str,
    item_ids: list[int],
    account: str | None = None,
    scope: str = "company",
    name: str | None = None,
    parent: str | None = None,
    *,
    actor: str,
    each: bool = False,
) -> Confirmed:
    """Add the items' labels to the dictionary and confirm the company's matching facts. `account`
    overrides the proposed one with an existing account. For a new-account proposal, the account
    is added first (`name` and `parent` override the proposed ones), in the same transaction as
    the label. Several ids are one all-or-nothing step, without their low-confidence items (see the
    module); with `each`, one step per id."""
    if scope not in SCOPES:
        raise InvalidMapping(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}")
    ids, skipped = _split_low(run, company, list(dict.fromkeys(item_ids)), each)
    done: list[tuple[int, int]] = []
    doc_ids: set[int] = set()
    for batch in [[item_id] for item_id in ids] if each else [ids]:
        items, changed = _confirm_batch(run, company, batch, account, scope, name, parent, actor)
        done += items
        doc_ids |= changed
    for item in skipped:
        run.event(
            "review",
            f"#{item['id']} {item['label']!r} not confirmed: confidence {item['confidence']} is "
            f"below {BULK_CONFIDENCE}, confirm it on its own",
            level="warning",
            code="E302",
            context={"company": company, "id": item["id"]},
        )
    return Confirmed(done, doc_ids, skipped)


def _per_share_change(old: str | None, new: str, accounts: dict[str, Account]) -> str:
    """How a fact's stored value changes when its account goes from `old` to `new`: 'to_printed'
    (rupees to a per-share figure as printed), 'to_rupees' or 'none'."""
    was = old is not None and accounts[old].unit == PER_SHARE
    now = accounts[new].unit == PER_SHARE
    if was == now:
        return "none"
    return "to_printed" if now else "to_rupees"


def _remapped(rows: list[tuple], key: tuple[str, str, str], own_sections: set[str]) -> list[tuple]:
    """The rows (fact_id, account, mapping_id, confirmed, section, label) the mapping `key` applies
    to: the same label, under its section (any section when it has none, except a section the
    company maps on its own)."""
    _, section, label = key
    found = []
    for row in rows:
        heading = normalize_label(row[4])
        if normalize_label(row[5]) != label:
            continue
        if (section and heading != section) or (not section and heading in own_sections):
            continue
        found.append(row)
    return found


def remap_facts(
    run: Run, company: str, statement: str, section: str, label: str, account: str
) -> tuple[int, set[int]]:
    """Apply the company's mapping of a label (just set with `review.set_mapping`) to its facts in
    loaded and held documents, in one transaction: account, mapping_id and confirmed, with the value
    turned to or from a per-share figure as printed when the unit changes. Returns (facts changed,
    their doc_ids); the caller checks those documents again (`fin.findings.recheck_held`)."""
    key = label_key(statement, section, label)
    label_map = load_label_map(run.root, company)
    mapping = label_map[(True, *key)]
    same_label = [k for k in label_map if k[0] and k[1] == statement and k[3] == key[2]]
    own_sections = {k[2] for k in same_label} - {key[1]}  # its other sections for this company
    accounts = load_accounts(run.root)
    changed, doc_ids = 0, set()
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        copy_review_accounts(run.root, con)
        rows = con.execute(
            "SELECT f.fact_id, f.account, f.mapping_id, f.confirmed, r.section, r.raw_label "
            "FROM facts f JOIN raw_values r USING (raw_id) JOIN documents d ON d.doc_id = f.doc_id "
            "WHERE d.status IN ('loaded', 'held') AND r.statement = ?",
            [statement],
        ).fetchall()
        for fact_id, old, mapping_id, confirmed, _, _ in _remapped(rows, key, own_sections):
            if (old, mapping_id, confirmed) == (account, mapping.mapping_id, True):
                continue
            [(doc_id,)] = con.execute(
                "UPDATE facts SET account = ?, mapping_id = ?, confirmed = true, value = CASE ? "
                "WHEN 'to_printed' THEN round(value / r.rupees_per_unit, 2) "
                "WHEN 'to_rupees' THEN value * r.rupees_per_unit ELSE value END "
                "FROM raw_values r WHERE r.raw_id = facts.raw_id AND facts.fact_id = ? "
                "RETURNING facts.doc_id",
                [account, mapping.mapping_id, _per_share_change(old, account, accounts), fact_id],
            ).fetchall()
            changed += 1
            doc_ids.add(doc_id)
    return changed, doc_ids
