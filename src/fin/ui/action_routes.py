"""The routes that change data: `POST /act` (a decision) and `POST /upload` (files into the inbox).
Both need the token cookie (middleware) and an Origin that is this server (`fin.ui.csrf`). The
answer is the screen again, with the outcome on top (code and run_id when it failed)."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from fin.audit.errors import UiActionInvalid
from fin.ui import actions, screens
from fin.ui.uploads import MAX_REQUEST_BYTES


def add_action_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
) -> None:
    def shown(
        request: Request, screen: str, company: str, outcome: actions.Outcome
    ) -> HTMLResponse:
        context = {
            "company": company,
            "data": screens.LOADERS[screen](root, company),
            "screen": screen,
            "outcome": outcome,
        }
        return templates.TemplateResponse(request, "_acted.html", context)

    @app.post("/act", response_class=HTMLResponse)
    def act(
        request: Request,
        action: str = Form(),
        company: str = Form(),
        item: str = Form(),
        reason: str = Form(""),
    ) -> HTMLResponse:
        chosen = known(root, company) or ""
        spec = actions.ACTIONS.get(action)
        try:
            outcome = actions.decide(root, chosen, action, item, reason)
        except UiActionInvalid as error:
            outcome = actions.Outcome(False, [error.message], None, error.code)
        return shown(request, spec.screen if spec else "home", chosen, outcome)

    @app.post("/upload", response_class=HTMLResponse)
    async def upload(request: Request) -> HTMLResponse:
        # A refusal before the company is known is shown as an error alert (E008, E001).
        if int(request.headers.get("content-length", "-1")) not in range(MAX_REQUEST_BYTES):
            raise UiActionInvalid("the upload is empty, of unknown size or too large")
        form = await request.form()
        chosen = known(root, str(form.get("company", ""))) or ""
        sent = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        try:
            outcome = actions.upload(root, chosen, [(f.filename or "", f.file) for f in sent])
        except UiActionInvalid as error:
            outcome = actions.Outcome(False, [error.message], None, error.code)
        return shown(request, "files", chosen, outcome)
