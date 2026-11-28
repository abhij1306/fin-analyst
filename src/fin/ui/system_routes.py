"""The routes SPEC-11 adds beside the screens: a saved question's evidence page (`/evidence-file`),
the built dashboard site (`/dashboard-site/...`, shown in the Dashboards screen) and the edit of a
draft FDD finding (`POST /fdd-edit`, as `fin fdd finding update`). All need the token cookie; the
POST needs the Origin too (`fin.ui.csrf`)."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from fin.actor import USER
from fin.audit.errors import DependencyMissing, UiActionInvalid
from fin.dashboard import BASE_PATH, last_good_build_dir
from fin.services import fdd
from fin.services.questions import evidence_page
from fin.services.result import ActionResult, Refused
from fin.ui import screens
from fin.ui.actions import ITEM_ID

REPORT_CSP = "sandbox allow-scripts allow-popups allow-downloads"
# The fields of a draft finding the Findings screen edits (`fin fdd finding update` options).
FDD_FIELDS = ("area", "title", "observation", "implication", "severity", "impact_type",
              "deal_consideration", "evidence")  # fmt: skip


def dashboard_file(root: Path, path: str) -> Path:
    """A file of the built dashboard site: a folder is its index.html. E001 for a path that is not
    in the build (one that leaves the folder too) or before the first build."""
    base = last_good_build_dir(root).resolve()
    target = (base / path).resolve()
    if target.is_relative_to(base) and target.is_dir():
        target = target / "index.html"
    if not target.is_relative_to(base) or not target.is_file():
        raise DependencyMissing(
            f"no dashboard page {path[:80]!r}; build the dashboard on the Dashboards screen"
        )
    return target


def finding_fields(form: dict[str, str]) -> dict[str, str]:
    """The fields the user filled in (blank ones stay as they are). E008 when none is."""
    fields = {name: form[name].strip() for name in FDD_FIELDS if (form.get(name) or "").strip()}
    if not fields:
        raise UiActionInvalid("fill in at least one field; nothing changed")
    return fields


def add_system_routes(
    app: FastAPI,
    root: Path,
    templates: Jinja2Templates,
    known: Callable[[Path, str | None], str | None],
) -> None:
    @app.get("/evidence-file")
    def evidence_file(company: str, query_id: str, download: bool = False) -> FileResponse:
        """A saved question's evidence page: opened as a sandboxed page, or downloaded."""
        page = evidence_page(root, known(root, company) or "", query_id)
        if download:
            return FileResponse(page, filename=page.name)
        return FileResponse(page, headers={"Content-Security-Policy": REPORT_CSP})

    @app.get(BASE_PATH + "/{path:path}")
    def dashboard_site(path: str) -> FileResponse:
        return FileResponse(dashboard_file(root, path))

    @app.post("/fdd-edit", response_class=HTMLResponse)
    async def fdd_edit(request: Request) -> HTMLResponse:
        form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
        chosen = known(root, form.get("company", "")) or ""
        item = form.get("item", "")
        outcome: ActionResult
        try:
            if not ITEM_ID.fullmatch(item):
                raise UiActionInvalid(f"{item[:60]!r} is not a finding id")
            fields = finding_fields(form)
            outcome = fdd.edit_finding(root, chosen, int(item), fields, via="ui", actor=USER)
        except UiActionInvalid as error:
            outcome = Refused.before_run(error)
        context = {
            "company": chosen,
            "tab_counts": screens.tab_counts(root, chosen or None),
            "data": screens.LOADERS["findings"](root, chosen),
            "screen": "findings",
            "outcome": outcome,
        }
        return templates.TemplateResponse(request, "_acted.html", context)
