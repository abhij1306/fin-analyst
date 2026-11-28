"""The decisions only the user takes: confirm, reject or change mappings, accept findings, move
queries, accept or reject adjustments, approve a gate, mark FDD findings final or draft. The CLI
commands and the UI's buttons (`fin.ui.actions`) call these. `via` ("cli" or "ui") is recorded
in the run's arguments with `actor` (`fin.actor`: the UI passes 'user'); everything else in the
run record is the same whichever interface called."""

from pathlib import Path
from typing import Any

from fin.audit.errors import InvalidMapping
from fin.audit.logger import Run
from fin.diligence import adjustments, gates, queries
from fin.fdd import findings as fdd
from fin.findings import accept, recheck_held
from fin.map import review, review_confirm
from fin.map.dictionary import STATEMENTS, mapping_row
from fin.metrics.export import refresh_analytics
from fin.services.result import ActionResult, perform

QUERY_COMMANDS = {
    "approved": "query approve",
    "sent": "query sent",
    "closed": "query close",
    "withdrawn": "query withdraw",
}
ADJUSTMENT_COMMANDS = {"accepted": "adjust accept", "rejected": "adjust reject"}


def _rechecked(run: Run, company: str, changed: set[int]) -> list[str]:
    """Check held documents and those in `changed` again; rebuild the shared metrics when figures
    changed. One line per document checked."""
    rechecked = recheck_held(run, company, changed)
    if changed or any(doc["status"] == "loaded" for doc in rechecked):
        refresh_analytics(run)
    return [
        f"doc_id {doc['doc_id']} {doc['file']} checked again: {doc['open']} open "
        f"findings, {doc['status']}"
        for doc in rechecked
    ]


def confirm_mappings(
    root: Path,
    company: str,
    ids: list[int],
    *,
    via: str,
    actor: str,
    account: str | None = None,
    scope: str = "company",
    name: str | None = None,
    parent: str | None = None,
    each: bool = False,
) -> ActionResult:
    """`fin review confirm`: confirm items (a low-confidence one only on its own or with `each`,
    `fin.map.review_confirm`), then check held and changed documents again and rebuild the shared
    metrics when figures changed."""

    def body(run: Run) -> list[str]:
        done = review_confirm.confirm(
            run, company, ids, account, scope, name, parent, actor=actor, each=each
        )
        lines = [
            f"#{i} confirmed ({scope} scope); {facts} facts updated" for i, facts in done.items
        ]
        lines += [
            f"#{item['id']} not confirmed: confidence {item['confidence']} is below "
            f"{review_confirm.BULK_CONFIDENCE}; confirm it on its own or with --each"
            for item in done.skipped
        ]
        return lines + _rechecked(run, company, done.doc_ids)

    args = {"company": company, "ids": ids, "account": account, "scope": scope}
    args |= {"name": name, "parent": parent, "each": each, "via": via, "actor": actor}
    return perform(root, "review confirm", args, body)


def _set_mapping(
    run: Run, company: str, key: tuple[str, str, str], account: str, actor: str
) -> list[str]:
    """Map the label for the company, apply it to the facts already loaded and check their
    documents again: no re-ingest."""
    statement, section, label = key
    if statement not in STATEMENTS:
        raise InvalidMapping(f"statement must be one of {', '.join(STATEMENTS)}")
    outcome = review.set_mapping(run, company, statement, label, account, section, actor=actor)
    facts, changed = review_confirm.remap_facts(run, company, statement, section, label, account)
    review.write_pending(run, company)
    where = f" under {section!r}" if section else ""
    lines = [
        f"{company}: {statement} {label!r}{where} -> {account} ({outcome}); {facts} loaded "
        f"facts in {len(changed)} documents now use it"
    ]
    return lines + _rechecked(run, company, changed)


def set_mapping(
    root: Path,
    company: str,
    key: tuple[str, str, str],
    account: str,
    *,
    via: str,
    actor: str,
) -> ActionResult:
    """`fin map set`: `key` is (statement, section, label as written)."""
    statement, section, label = key
    args = {"company": company, "statement": statement, "label": label, "account": account}
    args |= {"section": section, "via": via, "actor": actor}
    return perform(
        root, "map set", args, lambda run: _set_mapping(run, company, key, account, actor)
    )


def change_mapping(
    root: Path, company: str, ids: list[int], *, via: str, actor: str, account: str
) -> ActionResult:
    """The UI's "change the mapping" on a finding: `ids` is one label_map mapping_id (the one the
    finding names); its label is mapped to `account` for this company: `fin map set` with the
    label and section as the dictionary stores them, so the run record is the same. E304 for an
    unknown mapping or more than one."""
    try:
        key = mapping_row(root, ids[0]) if len(ids) == 1 else None
    except InvalidMapping:
        key = None
    if key is not None:
        return set_mapping(root, company, key, account, via=via, actor=actor)

    def refused(run: Run) -> list[str]:
        raise InvalidMapping(f"no mapping {ids} to change: name one mapping; nothing changed")

    args = {"company": company, "ids": ids, "account": account, "via": via, "actor": actor}
    return perform(root, "map set", args, refused)


def reject_mappings(
    root: Path, company: str, ids: list[int], *, via: str, actor: str
) -> ActionResult:
    """`fin review reject`: the items' facts stay unconfirmed."""

    def body(run: Run) -> list[str]:
        review.reject(run, company, ids, actor=actor)
        return [f"#{item_id} rejected" for item_id in dict.fromkeys(ids)]

    return perform(
        root, "review reject", {"company": company, "ids": ids, "via": via, "actor": actor}, body
    )


def accept_findings(
    root: Path, company: str, ids: list[int], *, via: str, actor: str
) -> ActionResult:
    """`fin findings accept`: save as is; a held document with no open findings left is loaded."""

    def body(run: Run) -> list[str]:
        lines = []
        for doc in accept(run, company, ids):
            where = (
                f"doc_id {doc['doc_id']}" if "doc_id" in doc else f"source file_id {doc['file_id']}"
            )
            lines.append(
                f"{company}/{doc['file']} ({where}): {doc['status']}, "
                f"{doc['open']} open findings left"
            )
        refresh = refresh_analytics(run)
        lines.append(
            f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
            f"{refresh.metric_rows} metric rows"
        )
        return lines

    return perform(
        root, "findings accept", {"company": company, "ids": ids, "via": via, "actor": actor}, body
    )


def set_query_status(
    root: Path,
    company: str,
    ids: list[int],
    target: str,
    *,
    via: str,
    actor: str,
    reason: str | None = None,
) -> ActionResult:
    """`fin query approve|sent|close|withdraw`: `target` is a key of QUERY_COMMANDS; a withdrawal
    carries the user's reason."""

    def body(run: Run) -> list[str]:
        rows = queries.set_status(run, company, ids, target, note=reason)
        return [queries.format_query(row) for row in rows]

    args: dict[str, Any] = {
        "company": company,
        "ids": ids,
        "reason": reason,
        "via": via,
        "actor": actor,
    }
    return perform(root, QUERY_COMMANDS[target], args, body)


def decide_adjustments(
    root: Path,
    company: str,
    ids: list[int],
    target: str,
    *,
    via: str,
    actor: str,
    reason: str | None = None,
) -> ActionResult:
    """`fin adjust accept|reject`: `target` is "accepted" or "rejected" (with the reason)."""

    def body(run: Run) -> list[str]:
        rows = adjustments.decide(run, company, ids, target, reason)
        return [adjustments.format_adjustment(row) for row in rows]

    args: dict[str, Any] = {
        "company": company,
        "ids": ids,
        "reason": reason,
        "via": via,
        "actor": actor,
    }
    return perform(root, ADJUSTMENT_COMMANDS[target], args, body)


def approve_gate(root: Path, company: str, gate: str, *, via: str, actor: str) -> ActionResult:
    """`fin gate approve`: `gate` is one of gates.NAMES (the caller checks it)."""

    def body(run: Run) -> list[str]:
        return [f"{company}: {gates.format_gate(gates.approve(run, company, gate))}"]

    return perform(
        root, "gate approve", {"company": company, "gate": gate, "via": via, "actor": actor}, body
    )


def mark_fdd_findings(
    root: Path, company: str, ids: list[int], *, via: str, actor: str, undo: bool = False
) -> ActionResult:
    """`fin fdd finding final [--undo]`: mark draft findings final, or return final ones to
    draft."""

    def body(run: Run) -> list[str]:
        rows = fdd.set_status(run, company, ids, "draft" if undo else "final")
        return [fdd.format_finding(row) for row in rows] or ["no FDD findings"]

    args = {"company": company, "ids": ids, "undo": undo, "via": via, "actor": actor}
    return perform(root, "fdd finding final", args, body)
