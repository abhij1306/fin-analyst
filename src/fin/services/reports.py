"""Reports and exports (the long actions the UI runs as jobs, `fin.services.jobs`)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fin.audit.logger import Run
from fin.report.dd_render import make_diligence_report
from fin.report.fdd_render import make_fdd_report
from fin.report.render import make_report
from fin.services.result import ActionResult, Progress, perform
from fin.workbook import export_workbook

# The reports `fin report` makes, by kind: health (no option), --diligence and --fdd (final findings
# only; a draft copy stays a `--drafts` run from chat or the terminal).
MAKERS: dict[str, Callable[[Run, str], dict[str, Any]]] = {
    "health": lambda run, company: make_report(run, company, None, False),
    "diligence": lambda run, company: make_diligence_report(run, company, None, False),
    "fdd": lambda run, company: make_fdd_report(run, company, None, False, False),
}
REPORT_KINDS = tuple(MAKERS)


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


def _relative(root: Path, path: str) -> str:
    return Path(path).relative_to(root).as_posix()


def report_lines(root: Path, result: dict[str, Any]) -> list[str]:
    """What a `fin report` result tells the user: the stub written and the next step, or the files
    rendered and the narrative references that had no value (shown as n/a in the report)."""
    if result["status"] == "stub_written":
        return [
            f"wrote the narrative stub {_relative(root, result['narrative_path'])}",
            result["next"],
        ]
    lines = [f"rendered {_relative(root, path)}" for path in result["outputs"]]
    if result.get("missing_values"):
        lines.append("references with no value (n/a): " + ", ".join(result["missing_values"]))
    return lines


def generate_report(
    root: Path, company: str, kind: str, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin report <company>` (kind health), `--diligence` or `--fdd`: the first run writes the
    narrative stub (Claude fills it in chat); the next one lints it and renders HTML, Word and PDF.
    `kind` is one of REPORT_KINDS (the interface checks it)."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress(f"making the {kind} report of {company}")
        return report_lines(run.root, MAKERS[kind](run, company))

    args = {"company": company, "scope": None, "render": False, "peers": None,
            "diligence": kind == "diligence", "fdd": kind == "fdd", "drafts": False,
            "via": via, "actor": actor}  # fmt: skip
    return perform(root, "report", args, body)
