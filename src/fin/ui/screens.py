"""The data of each screen, read with fin's own modules (read-only: no screen changes data). A
loader takes the data folder and a company and returns what its template shows. Numbers are
the ones fin returns; the UI adds none (docs/web-ui-plan.md)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, EngagementState
from fin.audit.logger import Run
from fin.audit.trace import company_history
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence import adjustments, gates, queries
from fin.diligence.engagement import rupees
from fin.fdd import findings as fdd
from fin.findings import list_findings
from fin.map.review import open_items, read_pending
from fin.paths import company_db_path, reports_dir
from fin.status import company_status
from fin.ui import views

REPORT_SUFFIXES = (".html", ".docx", ".pdf", ".xlsx")
LOG_LIMIT = 100
Loader = Callable[[Path, str], dict[str, Any]]


def _run(screen: str, root: Path, company: str) -> Run:
    # Screens only read: the run is not started, so there is no run record.
    return Run(f"ui {screen}", {"company": company}, root)


def _engagement(root: Path, company: str, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The screen's data, or why there is none: nothing loaded yet, or no open engagement (E803)."""
    if not company_db_path(root, company).is_file():
        return {"no_engagement": f"Nothing is loaded for {company} yet, so there is no engagement."}
    try:
        return load()
    except EngagementState as error:
        return {"no_engagement": f"{error.code}: {error.message}"}


def home(root: Path, company: str) -> dict[str, Any]:
    return views.home(company_status(_run("home", root, company), company))


def tab_counts(root: Path, company: str | None) -> dict[str, int]:
    """The counts on the tabs of the top bar for the company."""
    if company is None:
        return {}
    return views.tab_counts(company_status(_run("tabs", root, company), company))


def files(root: Path, company: str) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    path = company_db_path(root, company)
    if path.is_file():
        ensure_database(path, "company")
        with connect(path) as con:
            documents = fetch_dicts(
                con,
                "SELECT doc_id, file_name, version, fiscal_year, scope, status, ingested_at, "
                "reading_path, unread_pages FROM documents ORDER BY doc_id",
            )
    status = company_status(_run("files", root, company), company)
    return {
        "documents": documents,
        "inbox": status["inbox_not_ingested"],
        "inbox_folder": f"inbox/{company}/",
        "findings": list_findings(_run("files", root, company), company),
    }


def mappings(root: Path, company: str) -> dict[str, Any]:
    pending = read_pending(root, company)
    return {"pending": open_items(pending)}


def _query_rows(root: Path, company: str) -> dict[str, Any]:
    run = _run("queries", root, company)
    return {
        "queries": [
            {**q, "amount": rupees(q["amount"])} for q in queries.list_queries(run, company)
        ],
        "adjustments": [
            {**a, "amount": rupees(a["amount"])} for a in adjustments.list_adjustments(run, company)
        ],
    }


def queries_screen(root: Path, company: str) -> dict[str, Any]:
    return _engagement(root, company, lambda: _query_rows(root, company))


def gates_screen(root: Path, company: str) -> dict[str, Any]:
    return _engagement(
        root, company, lambda: gates.gate_status(_run("gates", root, company), company)
    )


def _fdd_rows(root: Path, company: str) -> dict[str, Any]:
    rows = fdd.list_findings(_run("findings", root, company), company)
    return {"findings": [{**r, "amount": rupees(r["amount"]), "flags": fdd.flags(r)} for r in rows]}


def findings_screen(root: Path, company: str) -> dict[str, Any]:
    return _engagement(root, company, lambda: _fdd_rows(root, company))


def report_files(root: Path, company: str) -> list[str]:
    """The reports and exports of the company, as paths under reports/<company>/."""
    base = reports_dir(root, company)
    if not base.is_dir():
        return []
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and not p.is_symlink() and p.suffix in REPORT_SUFFIXES
    )


def reports_screen(root: Path, company: str) -> dict[str, Any]:
    return {"files": report_files(root, company), "folder": f"reports/{company}/"}


def report_file(root: Path, company: str, name: str) -> Path:
    """The report file `name` (a path under reports/<company>/). E001 when it is not one of the
    listed files, so a path can never leave the folder."""
    if name not in report_files(root, company):
        raise DependencyMissing(f"no report file {name!r} for {company}")
    return reports_dir(root, company) / name


def log_screen(root: Path, company: str) -> dict[str, Any]:
    runs = company_history(root, company)["runs"]
    return {"runs": list(reversed(runs))[:LOG_LIMIT]}


LOADERS: dict[str, Loader] = {
    "home": home,
    "files": files,
    "mappings": mappings,
    "queries": queries_screen,
    "gates": gates_screen,
    "findings": findings_screen,
    "reports": reports_screen,
    "log": log_screen,
}
