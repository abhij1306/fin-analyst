"""The decisions a person takes in the UI and the upload of files (docs/web-ui-plan.md).

Each decision calls the function the matching CLI command calls, inside a run with the CLI's command
name: same run record, audit log, backup, gate checks, masking and refusals. The UI never drafts or
proposes. A refusal or error comes back as an Outcome with its code and run_id, not raised."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from fin.audit.errors import (
    EXIT_COMMAND_ERROR,
    EXIT_OK,
    FinError,
    UiActionInvalid,
)
from fin.audit.logger import Run
from fin.diligence import adjustments, gates, queries
from fin.fdd import findings as fdd
from fin.findings import accept, recheck_held
from fin.map import review
from fin.metrics.export import refresh_analytics
from fin.paths import inbox_dir
from fin.ui.uploads import save_uploads

ITEM_ID = re.compile(r"[0-9]{1,18}")  # a BIGINT id; ASCII digits only

Body = Callable[[Run, str, str, str | None], list[str]]


@dataclass(frozen=True)
class Outcome:
    ok: bool
    lines: list[str]
    run_id: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class Action:
    command: str  # the CLI command it stands for (its run's command name)
    screen: str  # the screen it is on, shown again afterwards
    body: Body
    reason: bool = False  # needs the user's reason, as the CLI's --reason does


def _query(target: str) -> Body:
    def body(run: Run, company: str, item: str, reason: str | None) -> list[str]:
        rows = queries.set_status(run, company, [int(item)], target, note=reason)
        return [queries.format_query(row) for row in rows]

    return body


def _adjustment(target: str) -> Body:
    def body(run: Run, company: str, item: str, reason: str | None) -> list[str]:
        rows = adjustments.decide(run, company, [int(item)], target, reason)
        return [adjustments.format_adjustment(row) for row in rows]

    return body


def _fdd_status(target: str) -> Body:
    def body(run: Run, company: str, item: str, reason: str | None) -> list[str]:
        rows = fdd.set_status(run, company, [int(item)], target)
        return [f"finding {row['finding_id']}: {row['status']}" for row in rows]

    return body


def _confirm_mapping(run: Run, company: str, item: str, reason: str | None) -> list[str]:
    confirmed, changed = review.confirm(run, company, [int(item)], None, "company", None, None)
    lines = [f"#{i} confirmed (company scope); {facts} facts updated" for i, facts in confirmed]
    rechecked = recheck_held(run, company, changed)
    lines += [
        f"doc_id {d['doc_id']} {d['file']} checked again: {d['open']} open findings, {d['status']}"
        for d in rechecked
    ]
    if changed or any(d["status"] == "loaded" for d in rechecked):
        refresh_analytics(run)
    return lines


def _reject_mapping(run: Run, company: str, item: str, reason: str | None) -> list[str]:
    review.reject(run, company, [int(item)])
    return [f"#{item} rejected"]


def _accept_finding(run: Run, company: str, item: str, reason: str | None) -> list[str]:
    docs = accept(run, company, [int(item)])
    refresh_analytics(run)
    return [f"{company}/{d['file']}: {d['status']}, {d['open']} open findings left" for d in docs]


def _approve_gate(run: Run, company: str, item: str, reason: str | None) -> list[str]:
    return [f"{company}: {gates.format_gate(gates.approve(run, company, item))}"]


ACTIONS: dict[str, Action] = {
    "mapping-confirm": Action("review confirm", "mappings", _confirm_mapping),
    "mapping-reject": Action("review reject", "mappings", _reject_mapping),
    "finding-accept": Action("findings accept", "files", _accept_finding),
    "query-approve": Action("query approve", "queries", _query("approved")),
    "query-sent": Action("query sent", "queries", _query("sent")),
    "query-close": Action("query close", "queries", _query("closed")),
    "query-withdraw": Action("query withdraw", "queries", _query("withdrawn"), reason=True),
    "adjust-accept": Action("adjust accept", "queries", _adjustment("accepted")),
    "adjust-reject": Action("adjust reject", "queries", _adjustment("rejected"), reason=True),
    "gate-approve": Action("gate approve", "gates", _approve_gate),
    "fdd-final": Action("fdd finding final", "findings", _fdd_status("final")),
    "fdd-draft": Action("fdd finding final", "findings", _fdd_status("draft")),
}


def perform(
    root: Path, command: str, args: dict[str, Any], body: Callable[[Run], list[str]]
) -> Outcome:
    """Run `body` as an audited run of `command`, like `fin.audit.logger.execute` but returning the
    outcome instead of exiting. A FinError is logged with its code and shown with the run_id."""
    run = Run(command, args, root)
    lines: list[str] = []
    code: str | None = None
    exit_code = EXIT_OK
    try:
        run.start(required=True)
        lines = body(run)
        exit_code = run.exit_code
    except FinError as exc:
        run.log_error(command, exc.message, exc.code, exc.context)
        lines, code, exit_code = [exc.message], exc.code, exc.exit_code
    except BaseException as exc:
        run.log_unexpected(command, exc)
        run.finish(EXIT_COMMAND_ERROR)
        raise
    exit_code = run.finish(exit_code)
    return Outcome(exit_code == EXIT_OK, lines, run.run_id, code)


def decide(root: Path, company: str, name: str, item: str, reason: str | None) -> Outcome:
    """Take one decision. E008 (no run) for an unknown action, a bad id or a missing reason."""
    action = ACTIONS.get(name)
    if action is None:
        raise UiActionInvalid(f"unknown action {name!r}")
    reason = (reason or "").strip() or None
    if action.reason and reason is None:
        raise UiActionInvalid("give the reason; nothing changed")
    if name == "gate-approve":
        item = item.upper()
        if item not in gates.NAMES:
            raise UiActionInvalid(f"unknown gate {item!r}; use one of {', '.join(gates.NAMES)}")
    elif not ITEM_ID.fullmatch(item):
        raise UiActionInvalid(f"{item!r} is not an item id")
    args = {"company": company, "ids": [item], "reason": reason, "via": "ui"}
    return perform(root, action.command, args, lambda run: action.body(run, company, item, reason))


def upload(root: Path, company: str, files: list[tuple[str, BinaryIO]]) -> Outcome:
    """Put files into the company's inbox folder. Ingest is not run: Claude ingests them in chat."""
    inbox = inbox_dir(root, company)

    def body(run: Run) -> list[str]:
        saved = save_uploads(inbox, files)
        for name in saved:
            run.event("ui upload", f"saved {name} to inbox/{company}/", file=f"{company}/{name}")
        return [f"{name} is waiting in inbox/{company}/" for name in saved] + [
            "Nothing is loaded yet: ask Claude to ingest the files."
        ]

    return perform(root, "ui upload", {"company": company, "files": [n for n, _ in files]}, body)
