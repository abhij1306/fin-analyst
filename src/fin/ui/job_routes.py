"""The UI's background jobs (`fin.services.jobs`): `POST /job` queues a long action and
`GET /partials/jobs` shows the company's jobs. The jobs card polls while one is queued or running;
it reads the jobs table, so it shows the same after a reload. A job's actor is always the user."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from fin.actor import USER
from fin.audit.errors import UiActionInvalid
from fin.services import reports
from fin.services.jobs import Jobs, Work, list_jobs
from fin.services.result import ActionResult, Progress

LABELS = {"export": "Export to Excel"}  # the job actions and how the page names them


def _export(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return reports.export_statements(root, company, via="ui", actor=USER, progress=progress)


WORK: dict[str, Work] = {"export": _export}


def jobs_context(root: Path, company: str) -> dict[str, Any]:
    return {"company": company, "jobs": list_jobs(root, company), "job_labels": LABELS}


def add_job_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
    jobs: Jobs,
) -> None:
    def shown(request: Request, company: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "_jobs.html", jobs_context(root, company))

    def chosen(company: str | None) -> str:
        name = known(root, company)
        if name is None:
            raise UiActionInvalid("there is no company yet; nothing was started")
        return name

    @app.post("/job", response_class=HTMLResponse)
    def job(
        request: Request, action: Annotated[str, Form()], company: Annotated[str, Form()]
    ) -> HTMLResponse:
        name = chosen(company)
        jobs.submit(name, action, {})
        return shown(request, name)

    @app.get("/partials/jobs", response_class=HTMLResponse)
    def partial(request: Request, company: str | None = None) -> HTMLResponse:
        return shown(request, chosen(company))
