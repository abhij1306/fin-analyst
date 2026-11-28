"""The data of the screens SPEC-11 adds: Questions, Dashboards, Settings and Health, and the Log
with its filters and a figure's trail. Read-only like `fin.ui.screens`; what changes data runs as a
job (`fin.ui.job_routes`). Settings and Health open without a company."""

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from fin.actor import label
from fin.audit.errors import FinError, SqlFailed
from fin.audit.trace import (
    company_history,
    fact_trace,
    format_fact_trace,
    format_metric_trace,
    metric_trace,
)
from fin.config import load_config
from fin.dashboard import last_good_build_dir
from fin.doctor import run_checks
from fin.extract.addon import ADDON_LABELS
from fin.extract.fallback import ABOUT
from fin.paths import audit_db_path, reports_dir
from fin.services.forensic import read_settings
from fin.services.questions import saved_questions
from fin.ui.actions import ITEM_ID
from fin.ui.job_routes import jobs_card

LOG_LIMIT = 100
NOT_RECORDED = "not recorded"  # the actor of a run whose arguments do not name one
ANY_COMPANY = ("settings", "health")  # screens that open before any company exists
WITH_QUERY = ("log",)  # loaders that also take the page's query parameters


def _jobs(root: Path, company: str | None) -> dict[str, Any]:
    """The jobs card: the company's jobs, or before any company the install's own (company "").
    `ready` once `fin init` made the audit database the jobs are kept in."""
    return {**jobs_card(root, company or ""), "ready": audit_db_path(root).is_file()}


def settings(root: Path, company: str | None) -> dict[str, Any]:
    """config.toml's display settings (the company's own when it has them) and PDF add-on, the data
    folder, and the add-ons on offer."""
    try:
        config = load_config(root)
    except FinError as error:
        config, problem = None, f"{error.code}: {error.message}"
    else:
        problem = None
    return {
        "data_folder": str(root),
        "display": config.display_for(company) if config else None,
        "display_own": bool(config and company in config.display_companies),
        "parser": config.pdf_parser if config else None,
        "problem": problem,
        "addons": [{"name": n, "label": t, "about": ABOUT[n]} for n, t in ADDON_LABELS.items()],
        "forensic": _forensic(root, company) if config and company else None,
        **_jobs(root, company),
    }


def _forensic(root: Path, company: str) -> dict[str, Any]:
    """The company's forensic settings (`fin forensic settings`), or why they cannot be read."""
    try:
        return {"rows": read_settings(root, company)}
    except FinError as error:
        return {"rows": [], "problem": f"{error.code}: {error.message}"}


def health(root: Path, company: str | None) -> dict[str, Any]:
    """`fin doctor`'s checks (each with its fix) and how many failed."""
    checks = [check.as_dict() for check in run_checks(root)]
    return {
        "checks": checks,
        "failed": sum(1 for c in checks if not c["ok"]),
        **_jobs(root, company),
    }


def questions(root: Path, company: str) -> dict[str, Any]:
    return {"questions": saved_questions(root, company), **_jobs(root, company)}


def dashboards(root: Path, company: str) -> dict[str, Any]:
    """Whether a dashboard build is there, and when it was made."""
    index = last_good_build_dir(root) / "index.html"
    built = datetime.fromtimestamp(index.stat().st_mtime) if index.is_file() else None
    return {"built_at": built, **_jobs(root, company)}


def narratives(root: Path, company: str) -> list[dict[str, Any]]:
    """The company's report narratives (`*narrative.md` under reports/<company>/) with their section
    headings, as Claude drafted them."""
    base = reports_dir(root, company)
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*narrative.md")):
        if path.is_symlink():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        sections = [line[3:].strip() for line in lines if line.startswith("## ")]
        out.append({"name": path.relative_to(base).as_posix(), "sections": sections})
    return out


def run_actor(run: dict[str, Any]) -> str:
    """The actor a run's arguments record (`user`, `ai:claude`), or NOT_RECORDED."""
    return json.loads(run["args"]).get("actor") or NOT_RECORDED


def _fact_id(text: str) -> int:
    if not ITEM_ID.fullmatch(text):
        raise SqlFailed(f"{text[:40]!r} is not a fact id (a whole number)")
    return int(text)


def trail(root: Path, company: str, query: Mapping[str, str]) -> dict[str, str] | None:
    """`fin audit` for one figure: `fact=<id>`, or `metric=<name>&period=<FY25>`. None when the
    page asks for none; the error code and message when there is no such figure."""
    fact, metric, period = query.get("fact"), query.get("metric"), query.get("period")
    try:
        if fact:
            return {"text": format_fact_trace(fact_trace(root, company, _fact_id(fact)))}
        if metric and period:
            return {"text": format_metric_trace(metric_trace(root, company, metric, period))}
    except FinError as error:
        return {"error": f"{error.code}: {error.message}"}
    return None


def log_screen(root: Path, company: str, query: Mapping[str, str]) -> dict[str, Any]:
    """The company's latest runs (newest first), filtered by command and actor, and a figure's trail
    when the page asks for one."""
    runs = list(reversed(company_history(root, company)["runs"]))
    for run in runs:
        run["actor"] = run_actor(run)
    command, actor = query.get("command") or "", query.get("actor") or ""
    shown = [r for r in runs if command in ("", r["command"]) and actor in ("", r["actor"])][
        :LOG_LIMIT
    ]
    actors = sorted({r["actor"] for r in runs})
    return {
        "runs": shown,
        "commands": sorted({r["command"] for r in runs}),
        "actors": [(a, a if a == NOT_RECORDED else label(a)) for a in actors],
        "command": command,
        "actor": actor,
        "trail": trail(root, company, query),
        "fact": query.get("fact") or "",
        "metric": query.get("metric") or "",
        "period": query.get("period") or "",
    }


LOADERS: dict[str, Callable[..., dict[str, Any]]] = {
    "questions": questions,
    "dashboards": dashboards,
    "settings": settings,
    "health": health,
    "log": log_screen,
}
