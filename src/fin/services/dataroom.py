"""The data-room intake as a service (SPEC-13): scan a folder into the plan, approve its rows, load
the approved ones. `fin dataroom scan|approve|load` and the Files screen call these; the UI runs
the scan and the load as jobs (`fin.services.jobs`) and approving as the user's decision."""

from pathlib import Path

from fin.audit.errors import DataroomPlanMissing
from fin.audit.logger import Run
from fin.config import load_config
from fin.dataroom.load import approve, load
from fin.dataroom.scan import PlanRow, read_plan, scan
from fin.metrics.export import refresh_analytics
from fin.services.result import ActionResult, Progress, perform


def plan_rows(root: Path, company: str) -> list[PlanRow]:
    """The company's plan; empty when it has none."""
    try:
        return read_plan(root, company)
    except DataroomPlanMissing:
        return []


def scan_folder(
    root: Path,
    company: str,
    folder: Path,
    *,
    via: str,
    actor: str,
    progress: Progress | None = None,
) -> ActionResult:
    """`fin dataroom scan`: add what is new in `folder` to the plan. Loads nothing."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress(f"scanning {folder.name}")
        found = scan(run, load_config(run.root), company, folder)
        return [
            f"scanned {found.folder}: {found.new} new in the plan, "
            f"{found.duplicates} new duplicate paths"
        ]

    args = {"company": company, "folder": str(folder), "via": via, "actor": actor}
    return perform(root, "dataroom scan", args, body)


def approve_rows(
    root: Path, company: str, ids: list[int], *, via: str, actor: str, undo: bool = False
) -> ActionResult:
    """`fin dataroom approve [--undo]`: the user's choice of rows to load."""

    def body(run: Run) -> list[str]:
        rows = approve(run, company, ids, undo=undo)
        return [
            f"[{r.file_id}] {r.path}: {'approved' if r.approved else 'not approved'}" for r in rows
        ]

    args = {"company": company, "ids": ids, "undo": undo, "via": via, "actor": actor}
    return perform(root, "dataroom approve", args, body)


def load_plan(
    root: Path, company: str, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin dataroom load`: every approved row not loaded yet, under one run id, one line per
    file; the shared metrics are rebuilt when something was loaded."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress("loading the approved files")
        done = load(run, load_config(run.root), company)
        if not done:
            return ["nothing approved is waiting to load (fin dataroom plan)"]
        lines = [f"[{i}/{len(done)}] {row.path}: {out}" for i, (row, out) in enumerate(done, 1)]
        refresh = refresh_analytics(run)
        lines.append(
            f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
            f"{refresh.metric_rows} metric rows"
        )
        return lines

    return perform(root, "dataroom load", {"company": company, "via": via, "actor": actor}, body)
