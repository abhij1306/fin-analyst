"""The UI server: a small FastAPI app that renders HTML pages (Jinja2, htmx, Basecoat) over fin's
own modules (docs/web-ui-plan.md). Needs the `ui` extra; `fin.cli.ui` imports this module only
when it is there. The screens are read-only; only the POST routes of `action_routes` change data."""

import json
import socket
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fin import __version__
from fin.audit.errors import DependencyMissing, FinError, UiUnavailable
from fin.fdd import findings as fdd_findings
from fin.services.jobs import Jobs
from fin.status import companies
from fin.ui import format as ui_format
from fin.ui import screens, system_screens
from fin.ui.action_routes import add_action_routes
from fin.ui.chat_routes import add_chat_routes
from fin.ui.chat_run import Chat
from fin.ui.csrf import origin_guard
from fin.ui.differences_screen import query_prompt
from fin.ui.diligence_routes import add_diligence_routes
from fin.ui.job_routes import WORK, add_job_routes
from fin.ui.nav import NAV_GROUPS, SCREENS
from fin.ui.security import new_token, protect
from fin.ui.setup import STATIC_DIR, TEMPLATE_DIR
from fin.ui.statements_screen import statement_source as source_of
from fin.ui.system_routes import REPORT_CSP, add_system_routes

DEFAULT_PORT = 8765


def known(root: Path, company: str | None) -> str | None:
    """The company, or the first one when none is chosen; E001 for one that does not exist."""
    names = companies(root)
    if not company:
        return names[0] if names else None
    if company not in names:
        raise DependencyMissing(
            f"no company {company!r}: put its files in inbox/{company}/ and ingest them"
        )
    return company


def report_response(path: Path) -> FileResponse:
    """HTML opens in the browser, the others download. A sandboxed page has an opaque origin: its
    scripts run, but it cannot use the token cookie or read the server's answers."""
    if path.suffix != ".html":
        return FileResponse(path, filename=path.name)
    return FileResponse(path, headers={"Content-Security-Policy": REPORT_CSP})


def _add_drawer_routes(app: FastAPI, root: Path, templates: Jinja2Templates) -> None:
    """A figure's source, the Differences screen's ask, and a report file."""

    @app.get("/partials/statement-source", response_class=HTMLResponse)
    def statement_source(request: Request, company: str) -> HTMLResponse:
        """One figure of the Statements screen: its source and marks, for the drawer."""
        chosen = known(root, company) or ""
        data = source_of(root, chosen, dict(request.query_params))
        return templates.TemplateResponse(request, "_source.html", {"data": data})

    @app.get("/partials/differences-ask")
    def differences_ask(
        company: str, item: Annotated[list[str] | None, Query()] = None
    ) -> Response:
        """The ticked rows of the Differences screen: the chat opens with a request to the
        assistant to draft one query for them (nothing is sent or changed here)."""
        text = query_prompt(root, known(root, company) or "", item or [])
        where = "/chat/panel?" + urlencode({"company": company, "prefill": text})
        target = json.dumps({"path": where, "target": "#chat", "swap": "outerHTML"})
        return Response(status_code=204, headers={"HX-Location": target})

    @app.get("/report-file")
    def report_file(company: str, name: str) -> FileResponse:
        """A report or export of the company: HTML opens in the browser, the others download."""
        return report_response(screens.report_file(root, known(root, company) or "", name))


def create_app(root: Path, token: str, port: int, claude: list[str] | None = None) -> FastAPI:
    """The app for one data folder. `claude` is the command that starts Claude Code (tests give a
    fake one); by default the `claude` on the path."""
    chat = Chat(root, claude)
    jobs = Jobs(root, WORK)
    jobs.recover()  # jobs the last server left unfinished are interrupted

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            chat.stop_all()  # a Claude process does not outlive the server

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.chat = chat
    app.middleware("http")(origin_guard(port))
    app.middleware("http")(protect(token, port))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATE_DIR)
    ui_format.register(templates.env)
    templates.env.globals["NAV"] = NAV_GROUPS
    templates.env.globals["FDD_CHOICES"] = fdd_findings.CHOICES

    @app.exception_handler(FinError)
    async def fin_error(request: Request, exc: FinError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "_error.html", {"error": f"{exc.code}: {exc.message}"}, status_code=400
        )

    def screen_data(screen: str, company: str | None, request: Request) -> dict[str, object]:
        loader = screens.LOADERS[screen]
        if screen in system_screens.ANY_COMPANY:
            return loader(root, company)  # type: ignore[arg-type]
        if company is None:
            return {"empty": True}
        if screen in screens.WITH_QUERY:
            return loader(root, company, dict(request.query_params))  # type: ignore[call-arg]
        return loader(root, company)

    def add_routes(screen: str) -> None:
        @app.get("/" if screen == "home" else f"/{screen}", response_class=HTMLResponse)
        def page(request: Request, company: str | None = None) -> HTMLResponse:
            chosen = known(root, company)
            context = {
                "companies": companies(root),
                "company": chosen,
                "tab_counts": screens.tab_counts(root, chosen),
                "screen": screen,
                "version": __version__,
                "data": screen_data(screen, chosen, request),
            }  # the partial gets the same "company" and "data"
            return templates.TemplateResponse(request, "screen.html", context)

        @app.get(f"/partials/{screen}", response_class=HTMLResponse)
        def partial(request: Request, company: str | None = None) -> HTMLResponse:
            chosen = known(root, company)
            context = {"company": chosen, "data": screen_data(screen, chosen, request)}
            return templates.TemplateResponse(request, f"_{screen}.html", context)

    for screen_id, _ in SCREENS:
        add_routes(screen_id)

    @app.get("/partials/file-rows", response_class=HTMLResponse)
    def file_rows(request: Request, company: str | None = None) -> HTMLResponse:
        """The Files screen's rows and findings: it asks again while a job runs."""
        chosen = known(root, company)
        context = {"company": chosen, "data": screen_data("files", chosen, request)}
        return templates.TemplateResponse(request, "_file_rows.html", context)

    _add_drawer_routes(app, root, templates)
    add_action_routes(app, root, templates, known)
    add_diligence_routes(app, root, templates, known)
    add_chat_routes(app, root, templates, chat)
    add_job_routes(app, root, templates, known, jobs)
    add_system_routes(app, root, templates, known)
    return app


def check_port(port: int) -> None:
    """Raises E006 when 127.0.0.1:port is taken."""
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise UiUnavailable(f"port {port} is in use; choose another with --port")


def serve(root: Path, port: int, open_browser: bool, announce: Callable[[str], None]) -> None:
    """Run the server until Ctrl-C. `announce(url)` is told the link with the token."""
    check_port(port)
    token = new_token()
    url = f"http://127.0.0.1:{port}/?token={token}"
    app = create_app(root, token, port)
    announce(url)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
