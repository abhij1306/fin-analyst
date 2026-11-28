"""FDD finding drafts (`fin fdd finding update|drop`, docs/fdd.md F8): editing and dropping a
draft, for the CLI and the web UI's Findings screen. Marking one final, or returning it to draft,
is the user's decision: `fin.services.decisions.mark_fdd_findings`."""

from pathlib import Path
from typing import Any

from fin.audit.logger import Run
from fin.fdd import findings
from fin.services.result import ActionResult, perform


def edit_finding(
    root: Path, company: str, finding_id: int, fields: dict[str, Any], *, via: str, actor: str
) -> ActionResult:
    """Change a draft's fields (E971 unless it is a draft; E970 for a field at fault)."""

    def body(run: Run) -> list[str]:
        return [findings.format_finding(findings.update(run, company, finding_id, fields))]

    args = {"company": company, "finding_id": finding_id, "fields": sorted(fields),
            "via": via, "actor": actor}  # fmt: skip
    return perform(root, "fdd finding update", args, body)


def drop_findings(
    root: Path, company: str, ids: list[int], *, via: str, actor: str
) -> ActionResult:
    """Drop draft findings: kept as dropped, out of the report (E971 unless each is a draft)."""

    def body(run: Run) -> list[str]:
        return [
            findings.format_finding(r) for r in findings.set_status(run, company, ids, "dropped")
        ]

    return perform(
        root, "fdd finding drop", {"company": company, "ids": ids, "via": via, "actor": actor}, body
    )
