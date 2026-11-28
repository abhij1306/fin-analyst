"""Saved questions: the read queries `fin sql` logged for a company (audit table `queries`), with
their SQL, row count and evidence page. The web UI's Questions screen lists them, opens or downloads
the evidence page and runs one again (`rerun_question`, a job)."""

from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.paths import audit_db_path
from fin.query.run import query
from fin.services.result import ActionResult, Progress, perform

SHOWN = 100  # the newest questions listed
COLUMNS = "query_id, run_id, recorded_at, question, sql, row_count, evidence_path"


def saved_questions(root: Path, company: str) -> list[dict[str, Any]]:
    """The company's read queries that ran (a row count), newest first. None before `fin init`."""
    path = audit_db_path(root)
    if not path.is_file():
        return []
    with connect(path) as con:
        rows = fetch_dicts(
            con,
            f"SELECT {COLUMNS} FROM queries WHERE company = ? AND mode = 'read' "
            "AND row_count IS NOT NULL ORDER BY recorded_at DESC LIMIT ?",
            [company, SHOWN],
        )
    return [row | {"has_evidence": _evidence(root, row) is not None} for row in rows]


def saved_question(root: Path, company: str, query_id: str) -> dict[str, Any]:
    """One saved question of the company. E001 when there is none with that id."""
    with connect(audit_db_path(root)) as con:
        rows = fetch_dicts(
            con,
            f"SELECT {COLUMNS} FROM queries WHERE company = ? AND mode = 'read' "
            "AND row_count IS NOT NULL AND query_id = ?",
            [company, query_id],
        )
    if not rows:
        raise DependencyMissing(f"{company} has no saved question {query_id!r}")
    return rows[0]


def _evidence(root: Path, row: dict[str, Any]) -> Path | None:
    """The question's evidence page when it is a file under evidence/ (resolved, so a stored path
    cannot leave the folder), else None."""
    if not row["evidence_path"]:
        return None
    folder = (root / "evidence").resolve()
    page = (root / row["evidence_path"]).resolve()
    return page if page.is_relative_to(folder) and page.is_file() else None


def evidence_page(root: Path, company: str, query_id: str) -> Path:
    """The evidence page of a saved question. E001 when it has none (not written, or deleted)."""
    page = _evidence(root, saved_question(root, company, query_id))
    if page is None:
        raise DependencyMissing(f"question {query_id} has no evidence page; run it again")
    return page


def rerun_question(
    root: Path, company: str, query_id: str, *, via: str, actor: str,
    progress: Progress | None = None,
) -> ActionResult:  # fmt: skip
    """Run a saved question's SQL again as a new `fin sql --evidence` run (a new query id and
    evidence page); the saved one stays as it was."""

    def body(run: Run) -> list[str]:
        saved = saved_question(run.root, company, query_id)
        if progress is not None:
            progress(f"running question {query_id} again")
        result = query(run, company, saved["question"], saved["sql"], evidence=True)
        lines = [f"{result['row_count']} rows; evidence page {result['evidence_path']}"]
        if result.get("rows_without_source"):
            rows = ", ".join(map(str, result["rows_without_source"]))
            lines.append(f"result rows with no source: {rows}")
        return lines

    args = {"company": company, "rerun_of": query_id, "evidence": True, "write": False,
            "via": via, "actor": actor}  # fmt: skip
    return perform(root, "sql", args, body)
