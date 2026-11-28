"""The data of each screen, read with fin's own modules (read-only: no screen changes data). A
loader takes the data folder and a company and returns what its template shows. Numbers are
the ones fin returns; the UI adds none (docs/web-ui-plan.md)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing, FinError
from fin.audit.trace import company_history
from fin.config import SCOPES
from fin.dataroom.load import waiting
from fin.dataroom.scan import plan_groups
from fin.diligence import adjustments, engagement, gates, queries
from fin.diligence.engagement import rupees
from fin.fdd import findings as fdd
from fin.findings import list_findings
from fin.map.dictionary import load_accounts
from fin.map.review import open_items, read_pending
from fin.map.review_confirm import BULK_CONFIDENCE, low_confidence
from fin.paths import company_db_path, dictionary_db_path, reports_dir
from fin.reference.compare import comparison
from fin.reference.lines import list_lines
from fin.services.addon import offer
from fin.services.dataroom import plan_rows
from fin.services.jobs import list_jobs
from fin.services.reviews import KINDS
from fin.status import company_status
from fin.ui import diligence_screens, views
from fin.ui.actions import MAX_IDS
from fin.ui.differences_screen import differences_screen
from fin.ui.diligence_screens import screen_run, with_engagement
from fin.ui.file_rows import company_name, file_rows
from fin.ui.job_routes import LABELS as JOB_LABELS
from fin.ui.statements_screen import statements_screen

REPORT_SUFFIXES = (".html", ".docx", ".pdf", ".xlsx")
LOG_LIMIT = 100
Loader = Callable[[Path, str], dict[str, Any]]


def home(root: Path, company: str) -> dict[str, Any]:
    return views.home(company_status(screen_run("home", root, company), company))


def tab_counts(root: Path, company: str | None) -> dict[str, int]:
    """The counts on the tabs of the top bar for the company."""
    if company is None:
        return {}
    return views.tab_counts(company_status(screen_run("tabs", root, company), company))


def files(root: Path, company: str) -> dict[str, Any]:
    status = company_status(screen_run("files", root, company), company)
    return {
        "rows": file_rows(root, company, status["inbox_not_ingested"]),
        "name": company_name(root, company),
        "inbox_folder": f"inbox/{company}/",
        "findings": [
            {**f, "mappings_list": json.loads(f["mappings"] or "[]")}
            for f in list_findings(screen_run("files", root, company), company)
        ],
        "accounts": _accounts(root),
        "jobs": list_jobs(root, company),
        "job_labels": JOB_LABELS,
        "addon_offer": offer(root, company),
        "plan": _plan(root, company),
    }


def _accounts(root: Path) -> dict[str, list[tuple[str, str]]]:
    """The dictionary's accounts per statement (account, description): the choices offered; none
    before `fin init`."""
    out: dict[str, list[tuple[str, str]]] = {}
    if not dictionary_db_path(root).is_file():
        return out
    for name, account in load_accounts(root).items():
        out.setdefault(account.statement, []).append((name, account.description))
    return out


def _review_kind(root: Path, company: str, kind: str) -> dict[str, Any]:
    """One review kind's waiting rows (as `fin review list --<kind>` prints them), or why it has
    none (nothing loaded, no open engagement: its code and message)."""
    spec = KINDS[kind]
    out: dict[str, Any] = {"kind": kind, "title": spec.title, "choice": spec.choice, "rows": []}
    if not company_db_path(root, company).is_file():
        return out
    try:
        rows = spec.waiting(screen_run("mappings", root, company), company)
    except FinError as error:
        return out | {"note": f"{error.code}: {error.message}"}
    out["rows"] = [{"id": row[spec.id_key], "text": spec.text(row)} for row in rows]
    return out


def _plan(root: Path, company: str) -> dict[str, Any]:
    """The data-room plan (SPEC-13): the files fin can use, with how many are approved and wait
    to load, and what will be skipped."""
    usable, _, skipped = plan_groups(plan_rows(root, company))
    return {"usable": usable, "skipped": skipped, "waiting": len(waiting(usable))}


def mappings(root: Path, company: str) -> dict[str, Any]:
    """Mapping items with a low-confidence flag (confirmed one at a time), the ids a bulk confirm
    takes (a proposal at confidence 0.8 or above; at most MAX_IDS, the rest by the next one), the
    dictionary's accounts per statement (to choose another), and every other review kind."""
    items = [{**i, "low": low_confidence(i)} for i in open_items(read_pending(root, company))]
    return {
        "pending": items,
        "bulk": [i["id"] for i in items if i["account"] and not i["low"]][:MAX_IDS],
        "bulk_confidence": BULK_CONFIDENCE,
        "accounts": _accounts(root),
        "kinds": [_review_kind(root, company, kind) for kind in KINDS],
    }


def _query_rows(root: Path, company: str) -> dict[str, Any]:
    run = screen_run("queries", root, company)
    opened = engagement.engagement_status(run, company)["engagement"]
    return {
        "periods": queries.periods_of(opened),
        "scopes": SCOPES,
        "queries": [
            {**q, "amount": rupees(q["amount"])} for q in queries.list_queries(run, company)
        ],
        "adjustments": [
            {**a, "amount": rupees(a["amount"])} for a in adjustments.list_adjustments(run, company)
        ],
    }


def queries_screen(root: Path, company: str) -> dict[str, Any]:
    return with_engagement(root, company, lambda: _query_rows(root, company))


def _gates(root: Path, company: str) -> dict[str, Any]:
    status = gates.gate_status(screen_run("gates", root, company), company)
    for gate in status["gates"]:
        for line in gate["checklist"]:
            line["fix"] = None if line["ok"] else views.gate_fix(line["text"])
    return status


def gates_screen(root: Path, company: str) -> dict[str, Any]:
    """Each gate's checklist; a failing line names the screen where it is closed (`fix`)."""
    return with_engagement(root, company, lambda: _gates(root, company))


def _fdd_rows(root: Path, company: str) -> dict[str, Any]:
    rows = fdd.list_findings(screen_run("findings", root, company), company)
    return {"findings": [{**r, "amount": rupees(r["amount"]), "flags": fdd.flags(r)} for r in rows]}


def findings_screen(root: Path, company: str) -> dict[str, Any]:
    return with_engagement(root, company, lambda: _fdd_rows(root, company))


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
    jobs = list_jobs(root, company)
    return {
        "files": report_files(root, company),
        "folder": f"reports/{company}/",
        "jobs": jobs,
        "job_labels": JOB_LABELS,
    }


def report_file(root: Path, company: str, name: str) -> Path:
    """The report file `name` (a path under reports/<company>/). E001 when it is not one of the
    listed files, so a path can never leave the folder."""
    if name not in report_files(root, company):
        raise DependencyMissing(f"no report file {name!r} for {company}")
    return reports_dir(root, company) / name


def references_screen(root: Path, company: str) -> dict[str, Any]:
    """Each mapped reference figure beside fin's (SPEC-10), and the lines waiting for a mapping."""
    if not company_db_path(root, company).is_file():
        return {"message": f"Nothing is loaded for {company} yet."}
    return {**comparison(root, company), "lines": list_lines(root, company)}


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
    "statements": statements_screen,
    "engagement": diligence_screens.engagement_screen,
    "bridge": diligence_screens.bridge_screen,
    "references": references_screen,
    "differences": differences_screen,
    "reports": reports_screen,
    "log": log_screen,
}
# Screens whose loader also takes the query parameters (the Statements and Differences pickers,
# the Bridge screen's view).
WITH_QUERY = ("statements", "bridge", "differences")
