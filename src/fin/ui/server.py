"""The UI server: a small FastAPI app that renders HTML pages (Jinja2, htmx, Basecoat) over fin's
own modules (docs/web-ui-plan.md). Needs the `ui` extra; `fin.cli.ui` imports this module only
when it is there. The screens are read-only; only the POST routes of `action_routes` change data."""

import socket
import webbrowser
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fin import __version__
from fin.audit.errors import DependencyMissing, FinError, UiUnavailable
from fin.services.jobs import Jobs
from fin.status import companies
from fin.ui import format as ui_format
from fin.ui import screens
from fin.ui.action_routes import add_action_routes
from fin.ui.chat_routes import add_chat_routes
from fin.ui.chat_run import Chat
from fin.ui.csrf import origin_guard
from fin.ui.job_routes import WORK, add_job_routes
from fin.ui.security import new_token, protect
from fin.ui.setup import STATIC_DIR, TEMPLATE_DIR
from fin.ui.statements_screen import statement_source as source_of

DEFAULT_PORT = 8765
REPORT_CSP = "sandbox allow-scripts allow-popups allow-downloads"
SCREENS = [
    (name.lower(), name)
    for name in (
        "Home",
        "Files",
        "Mappings",
        "Statements",
        "Queries",
        "Gates",
        "Findings",
        "Reports",
        "Log",
    )
]  # (id, label): the tabs of the top bar, in order


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
    templates.env.globals["SCREENS"] = SCREENS

    @app.exception_handler(FinError)
    async def fin_error(request: Request, exc: FinError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "_error.html", {"error": f"{exc.code}: {exc.message}"}, status_code=400
        )

    def screen_data(screen: str, company: str | None, request: Request) -> dict[str, object]:
        if company is None:
            return {"empty": True}
        if screen in screens.WITH_QUERY:
            query = dict(request.query_params)
            return screens.LOADERS[screen](root, company, query)  # type: ignore[call-arg]
        return screens.LOADERS[screen](root, company)

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

    @app.get("/partials/statement-source", response_class=HTMLResponse)
    def statement_source(request: Request, company: str) -> HTMLResponse:
        """One figure of the Statements screen: its source and marks, for the drawer."""
        chosen = known(root, company) or ""
        data = source_of(root, chosen, dict(request.query_params))
        return templates.TemplateResponse(request, "_source.html", {"data": data})

    @app.get("/report-file")
    def report_file(company: str, name: str) -> FileResponse:
        """A report or export of the company: HTML opens in the browser, the others download."""
        return report_response(screens.report_file(root, known(root, company) or "", name))

    add_action_routes(app, root, templates, known)
    add_chat_routes(app, root, templates, chat)
    add_job_routes(app, root, templates, known, jobs)
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
