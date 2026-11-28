"""The routes that change data: `POST /act` (a decision or a document deletion), `POST /upload`
(files into the inbox) and `POST /company` (a new company). All need the token cookie (middleware)
and an Origin that is this server (`fin.ui.csrf`). The answer is the screen again, with the
outcome on top (code and run_id when it failed); `respond` builds it for every form route."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.services.files import create_company, upload
from fin.services.result import ActionResult, Refused
from fin.ui import actions, screens
from fin.uploads import check_length

Known = Callable[[Path, str | None], str | None]


def acted(
    templates: Jinja2Templates,
    root: Path,
    request: Request,
    screen: str,
    company: str,
    outcome: ActionResult,
) -> HTMLResponse:
    """The screen again, with the outcome on top and the tab counts sent again."""
    context = {
        "company": company,
        "tab_counts": screens.tab_counts(root, company or None),
        "data": screens.LOADERS[screen](root, company) if company else {"empty": True},
        "screen": screen,
        "outcome": outcome,
    }
    return templates.TemplateResponse(request, "_acted.html", context)


def respond(
    templates: Jinja2Templates,
    root: Path,
    request: Request,
    screen: str,
    company: str,
    known: Known,
    act: Callable[[str], ActionResult],
) -> HTMLResponse:
    """Run `act` for the company and show `screen` with its outcome. A form that names no company
    runs nothing (E008), never the first company in its place; a refusal of the form's fields
    (E008) is shown like a refused run."""
    chosen = known(root, company) if company else None
    try:
        if not chosen:
            first = known(root, None)
            why = "choose the company" if first else "there is no company yet"
            raise UiActionInvalid(f"{why}; nothing changed")
        outcome = act(chosen)
    except UiActionInvalid as error:
        outcome = Refused.before_run(error)
    return acted(templates, root, request, screen, chosen or "", outcome)


def add_action_routes(app: FastAPI, root: Path, templates: Jinja2Templates, known: Known) -> None:
    _add_act_route(app, root, templates, known)
    _add_upload_route(app, root, templates, known)
    _add_company_route(app, root, templates)


def _add_act_route(app: FastAPI, root: Path, templates: Jinja2Templates, known: Known) -> None:
    @app.post("/act", response_class=HTMLResponse)
    def act(
        request: Request,
        action: str = Form(""),
        company: str = Form(""),
        item: str = Form(""),
        reason: str = Form(""),
        choice: str = Form(""),
    ) -> HTMLResponse:
        spec = actions.ACTIONS.get(action)
        return respond(
            templates,
            root,
            request,
            spec.screen if spec else "home",
            company,
            known,
            lambda co: actions.decide(root, co, action, item, reason, choice),
        )


def _add_upload_route(app: FastAPI, root: Path, templates: Jinja2Templates, known: Known) -> None:
    @app.post("/upload", response_class=HTMLResponse)
    async def upload_files(request: Request) -> HTMLResponse:
        check_length(request.headers.get("content-length"))  # an error alert (E008)
        form = await request.form()
        sent = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        files = [(f.filename or "", f.file) for f in sent]
        return respond(
            templates,
            root,
            request,
            "files",
            str(form.get("company", "")),
            known,
            lambda co: upload(root, co, files, via="ui", actor=USER),
        )


def _add_company_route(app: FastAPI, root: Path, templates: Jinja2Templates) -> None:
    @app.post("/company", response_class=HTMLResponse)
    def company(request: Request, code: str = Form(), name: str = Form()) -> HTMLResponse:
        """A new company: its Files screen opens (the top bar lists it), or the refusal shows."""
        try:
            outcome = create_company(root, code, name, via="ui", actor=USER)
        except UiActionInvalid as error:
            outcome = Refused.before_run(error)
        response = templates.TemplateResponse(request, "_outcome.html", {"outcome": outcome})
        if outcome.ok:
            response.headers["HX-Redirect"] = f"/files?company={code.strip()}"
        return response
