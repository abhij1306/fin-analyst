"""The decisions a person takes in the UI, the deletion of a document and the upload of files
(docs/web-ui-plan.md, docs/specs/SPEC-03).

Each entry names the service function its CLI command calls (`fin.services`): same run record
apart from `via`: audit log, backup, gate checks, masking and refusals. The actor is always the
user (`fin.actor.USER`), even when `fin ui` was started from inside Claude Code. The UI never
drafts or proposes (it edits and drops a draft finding, as `fin fdd finding update|drop`). A
refusal comes back as a Refused with its code and run_id, not raised."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.diligence import gates
from fin.services import dataroom, decisions, fdd, reviews
from fin.services.files import delete_documents, upload
from fin.services.result import ActionResult

ITEM_ID = re.compile(r"[0-9]{1,18}")  # a BIGINT id; ASCII digits only
MAX_IDS = 500  # the most ids one bulk decision takes

__all__ = ["ACTIONS", "Action", "decide", "upload"]


@dataclass(frozen=True)
class Action:
    screen: str  # the screen it is on, shown again afterwards
    service: Callable[..., ActionResult]
    given: dict[str, Any] = field(default_factory=dict)  # fixed arguments, e.g. the target status
    reason: bool = False  # needs the user's reason, as the CLI's --reason does
    choice: str | None = None  # the argument the user's chosen value goes to (an account, ...)
    needs_choice: bool = False  # the chosen value is required
    many: bool = False  # `item` may be several ids, comma-separated (a bulk decision)


def _kind_actions() -> dict[str, Action]:
    """Confirm and reject for each review kind other than mapping items (`fin.services.reviews`)."""
    out = {}
    for kind, spec in reviews.KINDS.items():
        given = {"kind": kind}
        choice = "choice" if spec.choice else None
        out[f"review-confirm-{kind}"] = Action(
            "mappings", reviews.confirm_items, given, choice=choice
        )
        out[f"review-reject-{kind}"] = Action("mappings", reviews.reject_items, given)
    return out


ACTIONS: dict[str, Action] = {
    "mapping-confirm": Action("mappings", decisions.confirm_mappings, choice="account"),
    "mapping-confirm-all": Action("mappings", decisions.confirm_mappings, many=True),
    "mapping-reject": Action("mappings", decisions.reject_mappings),
    "mapping-change": Action(
        "files", decisions.change_mapping, choice="account", needs_choice=True
    ),
    **_kind_actions(),
    "finding-accept": Action("files", decisions.accept_findings),
    "document-delete": Action("files", delete_documents),
    "query-approve": Action("queries", decisions.set_query_status, {"target": "approved"}),
    "query-sent": Action("queries", decisions.set_query_status, {"target": "sent"}),
    "query-close": Action("queries", decisions.set_query_status, {"target": "closed"}),
    "query-withdraw": Action(
        "queries", decisions.set_query_status, {"target": "withdrawn"}, reason=True
    ),
    "adjust-accept": Action("queries", decisions.decide_adjustments, {"target": "accepted"}),
    "adjust-reject": Action(
        "queries", decisions.decide_adjustments, {"target": "rejected"}, reason=True
    ),
    "gate-approve": Action("gates", decisions.approve_gate),
    "fdd-final": Action("findings", decisions.mark_fdd_findings),
    "fdd-draft": Action("findings", decisions.mark_fdd_findings, {"undo": True}),
    "reference-confirm": Action(
        "references", decisions.decide_reference_lines, {"target": "confirmed"}
    ),
    "reference-reject": Action(
        "references", decisions.decide_reference_lines, {"target": "rejected"}
    ),
    "dataroom-approve": Action("files", dataroom.approve_rows),
    "dataroom-undo": Action("files", dataroom.approve_rows, {"undo": True}),
    "fdd-drop": Action("findings", fdd.drop_findings),
}


def _ids(action: Action, item: str) -> list[int]:
    parts = item.split(",") if action.many else [item]
    if not 0 < len(parts) <= MAX_IDS or not all(ITEM_ID.fullmatch(p) for p in parts):
        raise UiActionInvalid(f"{item[:60]!r} is not an item id")
    return [int(p) for p in parts]


def _target(action: Action, item: str) -> str | list[int]:
    """A gate name for a gate approval, else the item ids."""
    if action.service is not decisions.approve_gate:
        return _ids(action, item)
    target = item.upper()
    if target not in gates.NAMES:
        raise UiActionInvalid(f"unknown gate {item!r}; use one of {', '.join(gates.NAMES)}")
    return target


def decide(
    root: Path, company: str, name: str, item: str, reason: str | None, chosen: str | None = None
) -> ActionResult:
    """Take one decision. E008 (no run) for an unknown action, a bad id or a missing reason or
    choice. `chosen` is the value the user picked (an account, a sector, a tax class)."""
    action = ACTIONS.get(name)
    if action is None:
        raise UiActionInvalid(f"unknown action {name!r}")
    reason = (reason or "").strip() or None
    chosen = (chosen or "").strip() or None
    if action.reason and reason is None:
        raise UiActionInvalid("give the reason; nothing changed")
    if action.needs_choice and chosen is None:
        raise UiActionInvalid("choose the account; nothing changed")
    if chosen is not None and action.choice is None:
        raise UiActionInvalid(f"{name} takes no choice; nothing changed")
    target = _target(action, item)
    given = action.given | ({"reason": reason} if action.reason else {})
    if action.choice and chosen is not None:
        given[action.choice] = chosen
    return action.service(root, company, target, via="ui", actor=USER, **given)
