"""The diligence forms of the UI (SPEC-09): open and close an engagement, import a walk sheet of
adjustments, and raise, edit, answer and export queries. Each calls the service its CLI command
calls (`fin.services.diligence`) with actor `user`; like `POST /act`, each needs the token cookie
and this server's Origin, and answers with the screen again and the outcome on top. The fields are
checked here (E008, no run) and the service refuses the rest as the CLI does."""

import tempfile
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.config import SCOPES
from fin.diligence import adjust_import, engagement
from fin.diligence.stub import STUB
from fin.services import diligence as services
from fin.services.result import ActionResult, Refused
from fin.ui import uploads
from fin.ui.action_routes import acted
from fin.ui.actions import ITEM_ID


def _required(value: str, name: str) -> str:
    text = value.strip()
    if not text:
        raise UiActionInvalid(f"give the {name}; nothing changed")
    return text


def _rupees(value: str, name: str) -> Decimal:
    try:
        return engagement.parse_rupees(_required(value, name))
    except ValueError as exc:
        raise UiActionInvalid(f"{name}: {exc}; nothing changed") from exc


def _item(value: str) -> int:
    if not ITEM_ID.fullmatch(value):
        raise UiActionInvalid(f"{value!r} is not an item id")
    return int(value)


def terms(periods: str, threshold: str, stub: str, due_days: str) -> services.Terms:
    """The form's engagement terms. E008 for periods, a threshold, a stub or days that are not
    valid (as the CLI's option checks)."""
    try:
        chosen = engagement.parse_periods(periods)
    except ValueError as exc:
        raise UiActionInvalid(f"{exc}; nothing opened") from exc
    amount = _rupees(threshold, "threshold") if threshold.strip() else None
    if amount is not None and amount < 0:
        raise UiActionInvalid("the threshold must be 0 or more rupees; nothing opened")
    name = stub.strip().upper() or None
    if name is not None and not STUB.match(name):
        raise UiActionInvalid("name the stub by its fiscal year and end month, e.g. FY27-YTD-SEP")
    days = due_days.strip()
    if days and not (days.isascii() and days.isdigit()):
        raise UiActionInvalid(f"credit terms {days!r}: give whole days, e.g. 90; nothing opened")
    return services.Terms(chosen, amount, name, int(days) if days else None)


def draft(amount: str, period: str, scope: str, source: str, question: str) -> services.QueryDraft:
    """The form's query. E008 for a missing field, an amount that is not rupees or a scope that
    is not one of SCOPES."""
    if scope and scope not in SCOPES:
        raise UiActionInvalid(f"scope {scope!r} is not one of {', '.join(SCOPES)}")
    return services.QueryDraft(
        _rupees(amount, "amount"),
        _required(period, "period"),
        scope or None,
        _required(source, "source"),
        _required(question, "question"),
    )


def add_diligence_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
) -> None:
    def respond(
        request: Request, screen: str, company: str, act: Callable[[str], ActionResult]
    ) -> HTMLResponse:
        chosen = known(root, company) or ""
        try:
            if not chosen:
                raise UiActionInvalid("there is no company yet; nothing changed")
            outcome = act(chosen)
        except UiActionInvalid as error:
            outcome = Refused.before_run(error)
        return acted(templates, root, request, screen, chosen, outcome)

    @app.post("/engagement/open", response_class=HTMLResponse)
    def engagement_open(
        request: Request,
        company: str = Form(),
        periods: str = Form(""),
        threshold: str = Form(""),
        stub: str = Form(""),
        due_days: str = Form(""),
    ) -> HTMLResponse:
        return respond(
            request,
            "engagement",
            company,
            lambda co: services.open_engagement(
                root, co, terms(periods, threshold, stub, due_days), via="ui", actor=USER
            ),
        )

    @app.post("/engagement/close", response_class=HTMLResponse)
    def engagement_close(request: Request, company: str = Form()) -> HTMLResponse:
        return respond(
            request,
            "engagement",
            company,
            lambda co: services.close_engagement(root, co, via="ui", actor=USER),
        )

    @app.post("/query/raise", response_class=HTMLResponse)
    def query_raise(
        request: Request,
        company: str = Form(),
        amount: str = Form(""),
        period: str = Form(""),
        scope: str = Form(""),
        source: str = Form(""),
        question: str = Form(""),
    ) -> HTMLResponse:
        return respond(
            request,
            "queries",
            company,
            lambda co: services.raise_query(
                root, co, draft(amount, period, scope, source, question), via="ui", actor=USER
            ),
        )

    @app.post("/query/edit", response_class=HTMLResponse)
    def query_edit(
        request: Request, company: str = Form(), item: str = Form(), question: str = Form("")
    ) -> HTMLResponse:
        return respond(
            request,
            "queries",
            company,
            lambda co: services.edit_query(
                root, co, _item(item), _required(question, "wording"), via="ui", actor=USER
            ),
        )

    @app.post("/query/answer", response_class=HTMLResponse)
    def query_answer(
        request: Request,
        company: str = Form(),
        item: str = Form(),
        text: str = Form(""),
        source: str = Form(""),
    ) -> HTMLResponse:
        def act(co: str) -> ActionResult:
            answer = services.Answer(_required(text, "answer"), source.strip() or None)
            return services.answer_query(root, co, _item(item), answer, via="ui", actor=USER)

        return respond(request, "queries", company, act)

    @app.post("/query/export", response_class=HTMLResponse)
    def query_export(
        request: Request, company: str = Form(), brief: str = Form("")
    ) -> HTMLResponse:
        return respond(
            request,
            "queries",
            company,
            lambda co: services.export_queries(root, co, brief=bool(brief), via="ui", actor=USER),
        )

    @app.post("/adjust/import", response_class=HTMLResponse)
    async def adjust_import_file(request: Request) -> HTMLResponse:
        if int(request.headers.get("content-length", "-1")) not in range(
            uploads.MAX_REQUEST_BYTES + 1
        ):
            raise UiActionInvalid("the upload is empty, of unknown size or too large")
        form = await request.form()
        sent = form.get("file")
        sheet = str(form.get("sheet", "")).strip() or None

        def act(co: str) -> ActionResult:
            if not isinstance(sent, UploadFile) or not sent.filename:
                raise UiActionInvalid("choose the walk sheet; nothing imported")
            name = uploads.safe_name(sent.filename, adjust_import.SUFFIXES)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / name
                uploads.save_one(sent.file, path)
                return services.import_adjustments(root, co, path, sheet, via="ui", actor=USER)

        return respond(request, "queries", str(form.get("company", "")), act)
