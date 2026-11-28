"""The install's own actions, for the web UI's Health and Dashboards screens and the CLI: update
fin (`fin update`) and build the dashboards (`fin dashboard --build`). Both are long, so the UI runs
them as jobs (`fin.services.jobs`). `fin doctor`'s checks are `fin.doctor.run_checks`."""

from pathlib import Path

from fin import dashboard
from fin.audit.logger import Run
from fin.services.result import ActionResult, Progress, perform
from fin.update import update


def update_fin(
    root: Path, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin update`: back up the databases, pull, sync, refresh the metrics and run `fin doctor`.
    A doctor that fails ends the run with exit 1: a Refused that keeps the lines (its failed
    checks and run id)."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress("backing up the databases, then git pull, uv sync and fin doctor")
        problems = update(run)
        if run.failed:
            return [*problems, f"update finished; fin doctor failed (update run_id {run.run_id})"]
        return ["update finished"]

    return perform(root, "update", {"via": via, "actor": actor}, body)


def build_dashboard(
    root: Path, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin dashboard --build`: refresh the dashboard data and build the static site into
    `<data>/dashboard/build` (E603 keeps the last good build; E001 without npm or its packages)."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress("refreshing the data and building the dashboard site (npm run build)")
        built = dashboard.build(run.root)
        return [f"dashboard built: {built.relative_to(run.root).as_posix()}"]

    return perform(root, "dashboard", {"build": True, "via": via, "actor": actor}, body)
