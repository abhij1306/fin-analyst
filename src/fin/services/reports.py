"""Reports and exports (the long actions the UI runs as jobs, `fin.services.jobs`)."""

from pathlib import Path

from fin.audit.logger import Run
from fin.services.result import ActionResult, Progress, perform
from fin.workbook import export_workbook


def export_statements(
    root: Path, company: str, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin export`: write reports/<company>/<company>_financials.xlsx."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress(f"writing reports/{company}/{company}_financials.xlsx")
        path = export_workbook(run.root, company)
        return [f"exported {path.relative_to(run.root).as_posix()}"]

    return perform(root, "export", {"company": company, "via": via, "actor": actor}, body)
