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
from fin.extract.addon import ADDON_LABELS
from fin.services import addon, reports
from fin.services.jobs import Jobs, Work, list_jobs
from fin.services.result import ActionResult, Progress

# The job actions and how the page names them.
LABELS = {"export": "Export to Excel", "addon": "Install a PDF add-on"}
CHOICES = {"addon": tuple(ADDON_LABELS)}  # the actions that take a choice, and its values


def _export(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return reports.export_statements(root, company, via="ui", actor=USER, progress=progress)


def _addon(root: Path, company: str, *, progress: Progress, name: str) -> ActionResult:
    return addon.install_addon(root, name, via="ui", actor=USER, progress=progress)


WORK: dict[str, Work] = {"export": _export, "addon": _addon}


def _arguments(action: str, choice: str | None) -> dict[str, Any]:
    """The job's arguments: its choice, for an action that takes one. E008 for a choice the action
    does not offer, or a choice given to one that takes none."""
    if action in CHOICES:
        if choice not in CHOICES[action]:
            raise UiActionInvalid(
                f"{action}: {choice!r} is not one of {', '.join(CHOICES[action])}"
            )
        return {"name": choice}
    if choice is not None:
        raise UiActionInvalid(f"{action} takes no choice; nothing was started")
    return {}


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
        request: Request,
        action: Annotated[str, Form()],
        company: Annotated[str, Form()],
        choice: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        name = chosen(company)
        jobs.submit(name, action, _arguments(action, choice))
        return shown(request, name)

    @app.get("/partials/jobs", response_class=HTMLResponse)
    def partial(request: Request, company: str | None = None) -> HTMLResponse:
        return shown(request, chosen(company))
