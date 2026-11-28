"""One row per file of a company for the Files screen (docs/specs/SPEC-03): each document (its
current version, loaded or held), each file that failed (the reason ingest wrote to
review/<company>/<file>.error.json) and each inbox file not loaded yet. Counts come from SQL; a
failed row carries its next step (`fin.services.next_steps`)."""

import json
from pathlib import Path
from typing import Any

from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.ingest import UNIT_ANSWERS
from fin.ingest_read import sha256_of
from fin.paths import company_db_path, inbox_dir, review_dir
from fin.services.next_steps import READ, NextStep, next_step, reading_prompt

DOCUMENTS = """
WITH counts AS (
    SELECT f.doc_id, count(*) AS facts,
        count(DISTINCT r.statement || '|' || r.raw_label) FILTER (WHERE f.account IS NULL)
            AS unmapped,
        count(*) FILTER (WHERE f.read_by LIKE 'ai:%') AS read_by_ai,
        count(*) FILTER (WHERE f.read_by NOT LIKE 'ai:%' AND f.read_by <> 'fin') AS read_by_addon
    FROM facts f JOIN raw_values r ON r.raw_id = f.raw_id
    GROUP BY f.doc_id
), open AS (
    SELECT doc_id, count(*) AS open_findings,
        array_to_string(list_sort(list(DISTINCT coalesce(code, check_name))), ', ') AS open_codes
    FROM findings WHERE status = 'open' GROUP BY doc_id
)
SELECT d.doc_id, d.file_name, d.sha256, d.version, d.fiscal_year, d.scope, d.status, d.ingested_at,
    d.unread_pages, d.run_id, coalesce(c.facts, 0) AS facts, coalesce(c.unmapped, 0) AS unmapped,
    coalesce(c.read_by_ai, 0) AS read_by_ai, coalesce(c.read_by_addon, 0) AS read_by_addon,
    coalesce(o.open_findings, 0) AS open_findings, o.open_codes
FROM documents d LEFT JOIN counts c USING (doc_id) LEFT JOIN open o USING (doc_id)
WHERE d.status <> 'superseded'
ORDER BY d.doc_id
"""
HELD = NextStep("Its findings are below: save it as is, or ask the assistant to fix it.")
UNREAD = NextStep(f"{READ} for those pages, or choose a PDF add-on.", ("reading", "addon"))
UNREAD_NO_COPY = NextStep(
    "Choose a PDF add-on, or upload the file again so the assistant can read those pages.",
    ("addon",),
)


def company_name(root: Path, company: str) -> str | None:
    """The name the company was created with on the Files screen, if it was."""
    path = company_db_path(root, company)
    if not path.is_file():
        return None
    with connect(path) as con:
        row = con.execute("SELECT name FROM company_profile ORDER BY created_at LIMIT 1").fetchone()
    return row[0] if row else None


def _inbox_copy(root: Path, company: str, doc: dict[str, Any]) -> str | None:
    """The loaded file's inbox copy (same name and content), relative to the data folder; a
    reading must load a file of that name, so the archived original (named by its sha256) will
    not do. None when the copy is gone."""
    copy = inbox_dir(root, company) / str(doc["file_name"])
    if copy.is_file() and sha256_of(copy) == doc["sha256"]:
        return copy.relative_to(root).as_posix()
    return None


def _document_row(root: Path, company: str, doc: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {**doc, "file": doc["file_name"], "state": doc["status"]}
    row["message"], row["next"], row["prompts"] = None, None, {}
    unread = json.loads(doc["unread_pages"]) if doc["unread_pages"] else []
    if doc["status"] == "held":
        row["code"] = doc["open_codes"]  # the checks that hold it (E401-E405)
        row["message"] = (
            f"{doc['open_findings']} open findings: not in current figures until they are "
            "accepted or fixed"
        )
        row["next"] = HELD
    elif unread:
        pages = ", ".join(f"{u['statement']} page {u['page']} ({u['code']})" for u in unread)
        row["message"] = f"statement pages fin did not read: {pages}"
        original = _inbox_copy(root, company, doc)
        row["next"] = UNREAD if original else UNREAD_NO_COPY
        if original:
            why = f"these statement pages were not read: {pages}"
            row["prompts"] = {"reading": reading_prompt(company, original, why, force=True)}
    return row


def _failed_row(root: Path, company: str, reason_file: Path) -> dict[str, Any]:
    name = reason_file.name.removesuffix(".error.json")
    try:
        reason = json.loads(reason_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reason = {"message": f"the reason file {reason_file.name} cannot be read: {exc}"}
    code = reason.get("code")
    step = next_step(code)
    original = (review_dir(root, company) / name).relative_to(root).as_posix()
    why = f"{code} {reason.get('message', '')}"
    prompts: dict[str, Any] = {"reading": reading_prompt(company, original, why, force=False)}
    return {
        "file": name,
        "state": "held" if reason.get("question") else "failed",  # held: it asks for the unit
        "code": code,
        "message": reason.get("message"),
        "run_id": reason.get("run_id"),
        "next": step,
        "prompts": prompts,
        "units": list(UNIT_ANSWERS) if "unit" in step.offers else [],  # each a Load with the unit
    }


def _loaded_since(copy: Path, loaded: set[str]) -> bool:
    """True when the failed file's content is loaded now (a reading of it, or a later ingest):
    content, not the name, since another file may have the same name."""
    return copy.is_file() and sha256_of(copy) in loaded


def file_rows(root: Path, company: str, waiting: list[str]) -> list[dict[str, Any]]:
    """Documents first, then failed files, then the inbox files waiting (`waiting`: the inbox
    files whose content is not loaded, from `fin.status`)."""
    documents: list[dict[str, Any]] = []
    path = company_db_path(root, company)
    if path.is_file():  # made and checked where it is created (fin.db.schema.ensure_database)
        with connect(path) as con:
            documents = fetch_dicts(con, DOCUMENTS)
    rows = [_document_row(root, company, doc) for doc in documents]
    loaded = {doc["sha256"] for doc in documents}
    review = review_dir(root, company)
    reasons = sorted(review.glob("*.error.json")) if review.is_dir() else []
    failed = [_failed_row(root, company, r) for r in reasons]
    failed = [row for row in failed if not _loaded_since(review / row["file"], loaded)]
    rows += failed
    shown = {row["file"] for row in failed}  # a failed inbox file is shown once, as failed
    message = f"waiting in inbox/{company}/: press Ingest"
    rows += [
        {"file": name, "state": "waiting", "code": None, "message": message, "next": None}
        for name in waiting
        if name not in shown
    ]
    return rows
