"""The Forensic screen (SPEC-18, Diligence menu): each test's state per period, then the flags of
the latest runs by test, largest first, each opening to its source and reason. Read-only: the run
button queues a job (`fin.ui.job_routes`), and the flags the user ticks go to the assistant's chat
as a request to draft one query (`fin query raise --flags`), as on the Differences screen. The
forensic settings are edited on the Settings screen (`POST /forensic/set`)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.diligence.engagement import rupees
from fin.services import forensic as services
from fin.ui import action_routes
from fin.ui.actions import ITEM_ID
from fin.ui.diligence_screens import screen_run, with_engagement
from fin.ui.job_routes import jobs_card

MAX_ASK = 50  # the most flags one request to the assistant names
OPEN = (None, "withdrawn")  # the query states a flag can be queried again in


def queryable(flag: dict[str, Any]) -> bool:
    """A flag of a current run, at or above the threshold, in no open query."""
    return bool(flag["over_threshold"]) and not flag["stale"] and flag["query_status"] in OPEN


def _load(root: Path, company: str) -> dict[str, Any]:
    run = screen_run("forensic", root, company)
    rows = services.read_status(run, root, company)
    flags = services.read_flags(run, root, company)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = groups.setdefault(row["test"], {"title": row["title"], "rows": [], "flags": []})
        group["rows"].append(row)
    for flag in flags:
        shown = {**flag, "amount_text": rupees(flag["amount"]), "queryable": queryable(flag)}
        groups[flag["test"]]["flags"].append(shown)
    return {"tests": groups, "count": len(flags), **jobs_card(root, company)}


def forensic_screen(root: Path, company: str) -> dict[str, Any]:
    """The screen's data, or why there is none (nothing loaded, no open engagement)."""
    return with_engagement(root, company, lambda: _load(root, company))


def query_prompt(root: Path, company: str, items: list[str]) -> str:
    """The chat message that asks the assistant to draft one query for the ticked flags. E008 when
    none is ticked, a flag cannot be queried, or the flags are of several periods."""
    ids = list(dict.fromkeys(items))
    if not 0 < len(ids) <= MAX_ASK or not all(ITEM_ID.fullmatch(i) for i in ids):
        raise UiActionInvalid(f"tick from 1 to {MAX_ASK} flags that can be queried")
    run = screen_run("forensic", root, company)
    flags = {str(f["flag_id"]): f for f in services.read_flags(run, root, company)}
    bad = [i for i in ids if i not in flags or not queryable(flags[i])]
    if bad:
        raise UiActionInvalid(
            f"flags {', '.join(f'#{i}' for i in bad)} cannot be queried: only a flag of a current "
            "run, at or above the threshold and in no open query, can be"
        )
    periods = sorted({flags[i]["period"] for i in ids})
    if len(periods) > 1:
        raise UiActionInvalid(
            f"the flags are of several periods ({', '.join(periods)}): one query per period"
        )
    return (
        f"Please draft one accountant query for {company} about the forensic flags #"
        f"{', #'.join(ids)} ({periods[0]}). Read them with `uv run fin forensic flags {company} "
        f"--json`, word the question neutrally (what the entries are, their source, why each "
        f"was flagged; no conclusion), and raise it with `uv run fin query raise {company} "
        f'--flags {",".join(ids)} --question "..."`. Do not approve or send it: I review it on '
        "the Queries screen."
    )


def add_forensic_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
) -> None:
    @app.get("/partials/forensic-ask")
    def forensic_ask(company: str, item: Annotated[list[str] | None, Query()] = None) -> Response:
        """The ticked flags: the chat opens with a request to draft one query (nothing changes)."""
        text = query_prompt(root, known(root, company) or "", item or [])
        where = "/chat/panel?" + urlencode({"company": company, "prefill": text})
        target = json.dumps({"path": where, "target": "#chat", "swap": "outerHTML"})
        return Response(status_code=204, headers={"HX-Location": target})

    @app.post("/forensic/set", response_class=HTMLResponse)
    def forensic_set(
        request: Request, company: str = Form(""), key: str = Form(""), value: str = Form("")
    ) -> HTMLResponse:
        """One forensic setting from the Settings screen, as `fin forensic set`."""

        def act(co: str) -> Any:
            if not value.strip():
                raise UiActionInvalid(f'give a value for {key[:40]!r} ("default" to reset)')
            return services.set_setting(root, co, key, value, via="ui", actor=USER)

        return action_routes.respond(templates, root, request, "settings", company, known, act)
