"""`fin status`: one view per company of what is loaded and what is waiting for the user.

For each company (every company database and every inbox/ company folder, or the one named):
- periods in current figures (years, halves, quarters, nine months) and the gaps between them (a
  year, half or quarter missing between the first and last loaded);
- held documents (fin.findings) with their open findings;
- findings open and accepted;
- mapping items waiting for review (review/<company>/pending.json);
- figures read by Claude not yet verified by an independent reading (`fin verify`);
- files in inbox/<company>/ not yet ingested (no document with the same sha256);
- with an open diligence engagement: its queries by status, proposed adjustments, and each gate's
  state (fin.diligence.gates).
"""

import re
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence.engagement import QUERY_STATUSES, open_engagement_row
from fin.diligence.gates import Context, evaluate
from fin.ingest import files_in, sha256_of
from fin.map.review import open_items, read_pending
from fin.paths import COMPANY_NAME, companies_dir, company_db_path, inbox_dir

PERIOD = re.compile(r"^FY(\d+)(?:-([QH])(\d))?$")
KINDS = {12: "years", 6: "halves", 3: "quarters", 9: "nine_months"}

_SUMMARY = """
SELECT
    (SELECT count(*) FROM findings WHERE status = 'open'),
    (SELECT count(*) FROM findings WHERE status = 'accepted'),
    (SELECT count(*) FROM facts f JOIN documents d USING (doc_id)
     WHERE f.read_by <> 'fin' AND NOT f.verified AND d.status IN ('loaded', 'held'))
"""
_HELD = """
SELECT d.doc_id, d.file_name,
       (SELECT count(*) FROM findings f WHERE f.doc_id = d.doc_id AND f.status = 'open')
FROM documents d WHERE d.status = 'held' ORDER BY d.doc_id
"""


def companies(root: Path) -> list[str]:
    """Every company with a database or an inbox/ folder."""
    names = {path.stem for path in companies_dir(root).glob("*.duckdb")}
    inbox = root / "inbox"
    if inbox.is_dir():
        names |= {p.name for p in inbox.iterdir() if p.is_dir() and COMPANY_NAME.match(p.name)}
    return sorted(names)


def gaps(periods: list[str]) -> list[str]:
    """Years, halves and quarters missing between the first and last of their kind in `periods`."""
    by_kind: dict[str, set[int]] = {"": set(), "H": set(), "Q": set()}
    for period in periods:
        match = PERIOD.match(period)
        if match is None:
            continue
        year, kind, part = int(match[1]), match[2] or "", int(match[3] or 1)
        per_year = {"": 1, "H": 2, "Q": 4}[kind]
        by_kind[kind].add(year * per_year + part - 1)
    missing = []
    for kind, per_year in (("", 1), ("H", 2), ("Q", 4)):
        found = by_kind[kind]
        for index in range(min(found, default=0), max(found, default=-1) + 1):
            if index not in found:
                year, part = divmod(index, per_year)
                missing.append(f"FY{year:02d}" + (f"-{kind}{part + 1}" if kind else ""))
    return missing


def company_status(run: Run, company: str) -> dict[str, Any]:
    db = company_db_path(run.root, company)
    periods: dict[str, list[str]] = {kind: [] for kind in KINDS.values()}
    held: list[dict[str, Any]] = []
    open_findings = accepted = unverified = 0
    known: set[str] = set()
    diligence = None
    pending = read_pending(run.root, company)
    if db.is_file():
        ensure_database(db, "company")
        with connect(db) as con:
            for period, months in con.execute(
                "SELECT DISTINCT period, period_months FROM current_facts ORDER BY period_months "
                "DESC, period"
            ).fetchall():
                periods[KINDS[months]].append(period)
            held = [
                {"doc_id": doc_id, "file": name, "open_findings": n}
                for doc_id, name, n in con.execute(_HELD).fetchall()
            ]
            row = con.execute(_SUMMARY).fetchone()
            assert row is not None
            open_findings, accepted, unverified = row
            known = {sha for (sha,) in con.execute("SELECT sha256 FROM documents").fetchall()}
            diligence = _diligence(con, company, open_items(pending))
    inbox = inbox_dir(run.root, company)
    waiting = (
        [p.name for p in files_in(inbox) if sha256_of(p) not in known] if inbox.is_dir() else []
    )
    return {
        "company": company,
        "periods": periods,
        "gaps": gaps([p for kind in ("years", "halves", "quarters") for p in periods[kind]]),
        "held_documents": held,
        "open_findings": open_findings,
        "accepted_findings": accepted,
        "pending_mappings": len(open_items(pending)),
        "unverified_claude_facts": unverified,
        "inbox_not_ingested": waiting,
        "diligence": diligence,
    }


def _diligence(
    con: duckdb.DuckDBPyConnection, company: str, pending: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The open engagement in short (None when there is none): queries by status (those with any),
    proposed adjustments, and each gate's state."""
    engagement = open_engagement_row(con)
    if engagement is None:
        return None
    engagement_id = engagement["engagement_id"]
    by_status = dict(
        con.execute(
            "SELECT status, count(*) FROM accountant_queries WHERE engagement_id = ? "
            "GROUP BY status",
            [engagement_id],
        ).fetchall()
    )
    proposed = con.execute(
        "SELECT count(*) FROM adjustments WHERE engagement_id = ? AND status = 'proposed'",
        [engagement_id],
    ).fetchone()
    assert proposed is not None
    states = evaluate(Context(con, company, engagement, pending))
    return {
        "engagement_id": engagement_id,
        "periods": engagement["periods"],
        "queries": {s: by_status[s] for s in QUERY_STATUSES if by_status.get(s)},
        "proposed_adjustments": proposed[0],
        "gates": {gate["gate"]: gate["state"] for gate in states},
    }


def status(run: Run, company: str | None) -> list[dict[str, Any]]:
    names = [company] if company is not None else companies(run.root)
    result = [company_status(run, name) for name in names]
    run.event("status", f"status of {len(result)} companies")
    return result


def _list(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def format_status(result: list[dict[str, Any]]) -> str:
    if not result:
        return "no companies yet: put files in inbox/<company>/ and run `fin ingest <company>`"
    out = []
    for s in result:
        c, p = s["company"], s["periods"]
        held = [
            f"doc_id {d['doc_id']} {d['file']} ({d['open_findings']} open)"
            for d in s["held_documents"]
        ]
        out += [
            c,
            f"  years: {_list(p['years'])}",
            f"  halves: {_list(p['halves'])}",
            f"  quarters: {_list(p['quarters'])}",
            f"  nine months: {_list(p['nine_months'])}",
            f"  gaps: {_list(s['gaps'])}",
            f"  held documents: {_list(held)}",
            f"  findings: {s['open_findings']} open, {s['accepted_findings']} accepted",
            f"  mappings waiting for review: {s['pending_mappings']}",
            "  figures read by Claude or Docling, not yet verified: "
            f"{s['unverified_claude_facts']}",
            f"  inbox files not ingested: {_list(s['inbox_not_ingested'])}",
        ]
        out += _format_diligence(s["diligence"])
    return "\n".join(out)


def _format_diligence(d: dict[str, Any] | None) -> list[str]:
    if d is None:
        return []
    queries = ", ".join(f"{status} {n}" for status, n in d["queries"].items()) or "none"
    gates = ", ".join(f"{name} {state}" for name, state in d["gates"].items())
    return [
        f"  engagement #{d['engagement_id']} ({d['periods']}): queries {queries}; "
        f"proposed adjustments {d['proposed_adjustments']}",
        f"  gates: {gates}",
    ]
