"""The decisions a person takes in the UI and the upload of files (docs/web-ui-plan.md).

Each entry names the service function its CLI command calls (`fin.services`): same run record
apart from `via`: audit log, backup, gate checks, masking and refusals. The actor is always the
user (`fin.actor.USER`), even when `fin ui` was started from inside Claude Code. The UI never
drafts or proposes. A refusal comes back as a Refused with its code and run_id, not raised."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.diligence import gates
from fin.services import decisions
from fin.services.files import upload
from fin.services.result import ActionResult

ITEM_ID = re.compile(r"[0-9]{1,18}")  # a BIGINT id; ASCII digits only

__all__ = ["ACTIONS", "Action", "decide", "upload"]


@dataclass(frozen=True)
class Action:
    screen: str  # the screen it is on, shown again afterwards
    service: Callable[..., ActionResult]
    given: dict[str, Any] = field(default_factory=dict)  # fixed arguments, e.g. the target status
    reason: bool = False  # needs the user's reason, as the CLI's --reason does


ACTIONS: dict[str, Action] = {
    "mapping-confirm": Action("mappings", decisions.confirm_mappings),
    "mapping-reject": Action("mappings", decisions.reject_mappings),
    "finding-accept": Action("files", decisions.accept_findings),
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
}


def decide(root: Path, company: str, name: str, item: str, reason: str | None) -> ActionResult:
    """Take one decision. E008 (no run) for an unknown action, a bad id or a missing reason."""
    action = ACTIONS.get(name)
    if action is None:
        raise UiActionInvalid(f"unknown action {name!r}")
    reason = (reason or "").strip() or None
    if action.reason and reason is None:
        raise UiActionInvalid("give the reason; nothing changed")
    target: str | list[int]
    if action.service is decisions.approve_gate:
        target = item.upper()
        if target not in gates.NAMES:
            raise UiActionInvalid(f"unknown gate {item!r}; use one of {', '.join(gates.NAMES)}")
    elif ITEM_ID.fullmatch(item):
        target = [int(item)]
    else:
        raise UiActionInvalid(f"{item!r} is not an item id")
    given = action.given | ({"reason": reason} if action.reason else {})
    return action.service(root, company, target, via="ui", actor=USER, **given)
