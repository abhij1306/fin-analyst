"""The UI's background jobs (`fin.services.jobs`): `POST /job` queues a long action and
`GET /partials/jobs` shows the company's jobs. The jobs card polls while one is queued or running;
it reads the jobs table, so it shows the same after a reload. A job's actor is always the user."""

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from fin.actor import USER
from fin.audit.errors import FinError, UiActionInvalid
from fin.extract.addon import ADDON_LABELS
from fin.ingest import UNIT_ANSWERS
from fin.paths import dataroom_dir
from fin.services import addon, dataroom, ingest, questions, reports, system
from fin.services.jobs import Jobs, Work, list_jobs
from fin.services.result import ActionResult, Progress
from fin.ui.actions import ITEM_ID
from fin.ui.uploads import check_length, save_uploads

# The job actions and how the page names them.
LABELS = {
    "export": "Export to Excel",
    "addon": "Install a PDF add-on",
    "ingest": "Ingest",
    "reingest": "Re-ingest",
    "unit": "Load with the unit",
    "dataroom-scan": "Scan a data room",
    "dataroom-load": "Load the approved files",
    "report": "Make a report",
    "dashboard": "Build the dashboard",
    "update": "Update fin",
    "rerun": "Run a question again",
}
# The actions that take a choice: its values and the argument it becomes.
CHOICES = {
    "addon": (tuple(ADDON_LABELS), "name"),
    "unit": (tuple(UNIT_ANSWERS), "unit"),
    "report": (reports.REPORT_KINDS, "kind"),
}
# The jobs that act on one item: a doc_id, a held file's name, or a saved question's query id
ON_ITEM = {"reingest", "unit", "rerun"}
# The jobs for the whole install: they run before any company exists, listed under company "".
INSTALL_WIDE = {"addon", "dashboard", "update"}


def _export(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return reports.export_statements(root, company, via="ui", actor=USER, progress=progress)


def _addon(root: Path, company: str, *, progress: Progress, name: str) -> ActionResult:
    return addon.install_addon(root, name, via="ui", actor=USER, progress=progress)


def _ingest(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return ingest.ingest_inbox(root, company, via="ui", actor=USER, progress=progress)


def _reingest(root: Path, company: str, *, doc_id: int, progress: Progress) -> ActionResult:
    return ingest.reingest_document(root, company, doc_id, via="ui", actor=USER, progress=progress)


def _unit(root: Path, company: str, *, file: str, unit: str, progress: Progress) -> ActionResult:
    return ingest.load_with_unit(root, company, file, unit, via="ui", actor=USER, progress=progress)


def _dataroom_scan(root: Path, company: str, *, folder: str, progress: Progress) -> ActionResult:
    return dataroom.scan_folder(
        root, company, Path(folder), via="ui", actor=USER, progress=progress
    )


def _dataroom_load(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return dataroom.load_plan(root, company, via="ui", actor=USER, progress=progress)


def _report(root: Path, company: str, *, kind: str, progress: Progress) -> ActionResult:
    return reports.generate_report(root, company, kind, via="ui", actor=USER, progress=progress)


def _dashboard(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return system.build_dashboard(root, via="ui", actor=USER, progress=progress)


def _update(root: Path, company: str, *, progress: Progress) -> ActionResult:
    return system.update_fin(root, via="ui", actor=USER, progress=progress)


def _rerun(root: Path, company: str, *, query_id: str, progress: Progress) -> ActionResult:
    return questions.rerun_question(
        root, company, query_id, via="ui", actor=USER, progress=progress
    )


WORK: dict[str, Work] = {
    "export": _export,
    "addon": _addon,
    "ingest": _ingest,
    "reingest": _reingest,
    "unit": _unit,
    "dataroom-scan": _dataroom_scan,
    "dataroom-load": _dataroom_load,
    "report": _report,
    "dashboard": _dashboard,
    "update": _update,
    "rerun": _rerun,
}


def job_arguments(
    root: Path, company: str, action: str, item: str, choice: str | None
) -> dict[str, Any]:
    """The job's arguments from the form. E008 for a choice the action does not offer, a choice
    or an item given to one that takes none, or a document or held file that does not exist."""
    arguments: dict[str, Any] = {}
    if action in CHOICES:
        values, argument = CHOICES[action]
        if choice not in values:
            raise UiActionInvalid(f"{action}: {choice!r} is not one of {', '.join(values)}")
        arguments[argument] = choice
    elif choice is not None:
        raise UiActionInvalid(f"{action} takes no choice; nothing was started")
    if action == "dataroom-scan":
        folder = Path(item.strip()).expanduser()
        if not item.strip() or not folder.is_dir():
            raise UiActionInvalid(f"{item!r} is not a folder on this computer; nothing started")
        return arguments | {"folder": str(folder)}
    if action not in ON_ITEM:
        if item:
            raise UiActionInvalid(f"{LABELS.get(action, action)} takes no item; nothing started")
        return arguments
    if action == "unit":
        ingest.held_for_unit(root, company, item)  # E008 now, not when the job runs
        return arguments | {"file": item}
    if action == "rerun":
        questions.saved_question(root, company, item)  # E001 now, not when the job runs
        return arguments | {"query_id": item}
    if not ITEM_ID.fullmatch(item):
        raise UiActionInvalid(f"{item!r} is not a document id; nothing was started")
    ingest.document(root, company, int(item))
    return arguments | {"doc_id": int(item)}


def jobs_card(root: Path, company: str) -> dict[str, Any]:
    """What the Jobs card shows: the company's latest jobs (company "" before any company exists:
    the install's own) and how each action is named. Every screen with the card uses this."""
    return {"jobs": list_jobs(root, company), "job_labels": LABELS}


def add_job_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
    jobs: Jobs,
) -> None:
    def shown(request: Request, company: str) -> HTMLResponse:
        context = {"company": company, **jobs_card(root, company)}
        return templates.TemplateResponse(request, "_jobs.html", context)

    def chosen(company: str | None, install_wide: bool = False) -> str:
        name = known(root, company)
        if name is None and install_wide:
            return ""  # no company yet: the install's own jobs
        if name is None:
            raise UiActionInvalid("there is no company yet; nothing was started")
        return name

    @app.post("/job", response_class=HTMLResponse)
    def job(
        request: Request,
        action: Annotated[str, Form()],
        company: Annotated[str, Form()] = "",
        item: Annotated[str, Form()] = "",
        choice: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        name = chosen(company, action in INSTALL_WIDE)
        jobs.submit(name, action, job_arguments(root, name, action, item, choice))
        response = shown(request, name)
        response.headers["HX-Trigger"] = "job-queued"  # the Files screen's rows start to follow it
        return response

    @app.post("/dataroom-upload", response_class=HTMLResponse)
    async def dataroom_upload(request: Request) -> HTMLResponse:
        """A data room as a .zip: saved in its own folder under dataroom/<company>/uploads/, then
        scanned as a job, as a chosen folder is."""
        check_length(request.headers.get("content-length"))
        form = await request.form()
        name = chosen(str(form.get("company", "")))
        sent = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        base = dataroom_dir(root, name) / "uploads"
        base.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(dir=base))
        try:
            save_uploads(folder, [(f.filename or "", f.file) for f in sent], (".zip",))
        except (FinError, OSError):
            shutil.rmtree(folder, ignore_errors=True)
            raise
        jobs.submit(name, "dataroom-scan", {"folder": str(folder)})
        response = shown(request, name)
        response.headers["HX-Trigger"] = "job-queued"
        return response

    @app.get("/partials/jobs", response_class=HTMLResponse)
    def partial(request: Request, company: str | None = None) -> HTMLResponse:
        return shown(request, chosen(company, install_wide=True))
