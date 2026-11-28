"""The PDF add-on at the point of need (docs/specs/SPEC-04): whether to offer one, and installing
one (`fin parser use`, the web UI's job)."""

import json
from pathlib import Path
from typing import Any

from fin.audit.errors import ScannedPdf
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect
from fin.extract.addon import ADDON_LABELS
from fin.extract.fallback import ABOUT, offered
from fin.parser import use
from fin.paths import company_db_path, review_dir
from fin.services.result import ActionResult, Progress, perform


def install_addon(
    root: Path, name: str, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin parser use <name>`: install the add-on, download its models, record it."""

    def body(run: Run) -> list[str]:
        if progress is not None:
            progress(f"installing {ABOUT[name]}")
        return use(run, name)

    return perform(root, "parser use", {"name": name, "via": via, "actor": actor}, body)


def offer(root: Path, company: str) -> list[dict[str, Any]]:
    """The add-ons to offer (name, label, about, cloud) when no add-on is chosen and a PDF of the
    company has statement pages fin did not read: in a loaded document, or in a file that failed
    (its review/ reason). For scanned PDFs only, the add-ons whose OCR fin uses. [] otherwise."""
    pages, scanned = _skipped(root, company)
    if not pages and not scanned:
        return []
    if load_config(root).pdf_parser != "none":
        return []
    return [
        {"name": n, "label": ADDON_LABELS[n], "about": ABOUT[n], "cloud": n == "llamaparse"}
        for n in offered(scanned and not pages)
    ]


def _skipped(root: Path, company: str) -> tuple[bool, bool]:
    """(some PDF statement page was not read, some file failed as a scanned PDF)."""
    entries: list[dict[str, Any]] = []
    path = company_db_path(root, company)
    if path.is_file():
        with connect(path) as con:
            rows = con.execute(
                "SELECT unread_pages FROM documents WHERE status IN ('loaded', 'held') "
                "AND unread_pages IS NOT NULL"
            ).fetchall()
        entries = [entry for (unread,) in rows for entry in json.loads(unread)]
    scanned = False
    folder = review_dir(root, company)
    for reason_file in sorted(folder.glob("*.error.json")) if folder.is_dir() else []:
        reason = json.loads(reason_file.read_text(encoding="utf-8"))
        entries.extend(reason.get("unread_pages") or [])
        scanned = scanned or reason.get("code") == ScannedPdf.code
    return any(entry.get("page") is not None for entry in entries), scanned
