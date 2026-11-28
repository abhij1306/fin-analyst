"""`fin status`: one view per company of what is loaded and what is waiting for the user.

For each company (every company database and every inbox/ company folder, or the one named):
- periods in current figures (years, halves, quarters, nine months) and the gaps between them (a
  year, half or quarter missing between the first and last loaded);
- held documents (fin.findings) with their open findings;
- findings open and accepted;
- mapping items waiting for review (review/<company>/pending.json);
- checks that did not run on loaded and held documents, and what would make them run
  (fin.validate.not_run);
- figures in loaded and held documents read by Claude (a reading file) or by the PDF add-on,
  as information: whether they are trusted is the user's decision (`fin verify` checks them);
- figures the user stated where no document gives them, and figures corrected with `fin fact
  correct` (fin.facts), as information;
- files in inbox/<company>/ not yet ingested (no document with the same sha256);
- with an open diligence engagement: its queries by status, proposed adjustments, and each gate's
  state (fin.diligence.gates);
- how many items of each kind wait for the user (WAITING): the owner of the counts `fin status`,
  the Home screen and the UI's tab counts show.
"""

import re
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.dataroom.scan import awaiting_approval, plan_of
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import customers, ledgers
from fin.diligence.engagement import QUERY_STATUSES, open_engagement_row
from fin.diligence.gates import Context, evaluate
from fin.fdd import tax_ledgers
from fin.ingest import files_in, sha256_of
from fin.map.review import open_items, read_pending
from fin.paths import COMPANY_NAME, companies_dir, company_db_path, inbox_dir
from fin.validate.not_run import format_not_run, list_not_run

PERIOD = re.compile(r"^FY(\d+)(?:-([QH])(\d))?$")
KINDS = {12: "years", 6: "halves", 3: "quarters", 9: "nine_months"}
# Everything that waits for the user, in the order `fin status` and the Home screen list it. The
# review kinds use `fin review list --<kind>`'s names (fin.services.reviews.KINDS).
WAITING = {
    "mappings": "mapping items to review",
    "ledgers": "Tally ledgers to review",
    "customers": "customer sectors to review",
    "tax": "tax ledger classes to review",
    "related": "related party links to review",
    "netdebt": "net debt classes to review",
    "matches": "bank matches to review",
    "reference_lines": "proposed reference lines to confirm or reject",
    "open_findings": "open findings on filed data",
    "inbox": "files in the inbox not loaded yet",
    "dataroom": "data-room files waiting for approval",
    "queries": "queries to approve, mark sent or close",
    "adjustments": "proposed adjustments to decide",
    "fdd_drafts": "draft FDD findings to mark final or drop",
    "provisions": "provision ledgers to confirm or reject",
    "netdebt_items": "net debt items to accept or reject",
}
# Query statuses that wait for the user: approve a draft, mark an approved one sent, close an
# answered one.
QUERY_ACTIONABLE = ("draft", "approved", "answered")
_COMPANY_WAITING = """
SELECT
    (SELECT count(*) FROM netdebt_classes WHERE status = 'proposed'),
    (SELECT count(*) FROM reference_lines WHERE status = 'proposed')
"""
_ENGAGEMENT_WAITING = """
SELECT
    (SELECT count(*) FROM related_party_links WHERE engagement_id = $1 AND status = 'proposed'),
    (SELECT count(*) FROM match_proposals WHERE engagement_id = $1 AND status = 'proposed'),
    (SELECT count(*) FROM fdd_findings WHERE engagement_id = $1 AND status = 'draft'),
    (SELECT count(*) FROM netdebt_items WHERE engagement_id = $1 AND status = 'proposed'),
    (SELECT count(*) FROM provision_ledgers WHERE status = 'proposed')
"""

_SUMMARY = """
SELECT
    (SELECT count(*) FROM findings WHERE status = 'open'),
    (SELECT count(*) FROM findings WHERE status = 'accepted'),
    (SELECT count(*) FROM facts f JOIN documents d USING (doc_id)
     WHERE f.read_by LIKE 'ai:%' AND d.status IN ('loaded', 'held')),
    (SELECT count(*) FROM facts f JOIN documents d USING (doc_id)
     WHERE f.read_by <> 'fin' AND f.read_by NOT LIKE 'ai:%' AND d.status IN ('loaded', 'held')),
    (SELECT count(*) FROM stated_now),
    (SELECT count(*) FROM corrected_now)
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
    open_findings = accepted = by_claude = by_addon = stated = corrected = 0
    known: set[str] = set()
    diligence = None
    not_run: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
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
            open_findings, accepted, by_claude, by_addon, stated, corrected = row
            not_run = list_not_run(con)
            known = {sha for (sha,) in con.execute("SELECT sha256 FROM documents").fetchall()}
            diligence = _diligence(con, company, open_items(pending))
            counts = _counts(con, diligence)
    inbox = inbox_dir(run.root, company)
    waiting = (
        [p.name for p in files_in(inbox) if sha256_of(p) not in known] if inbox.is_dir() else []
    )
    counts |= {
        "mappings": len(open_items(pending)),
        "open_findings": open_findings,
        "inbox": len(waiting),
    }
    return {
        "company": company,
        "periods": periods,
        "gaps": gaps([p for kind in ("years", "halves", "quarters") for p in periods[kind]]),
        "held_documents": held,
        "open_findings": open_findings,
        "accepted_findings": accepted,
        "pending_mappings": len(open_items(pending)),
        "checks_not_run": not_run,
        "read_by_claude": by_claude,
        "read_by_pdf_addon": by_addon,
        "stated_figures": stated,
        "corrected_figures": corrected,
        "inbox_not_ingested": waiting,
        "diligence": diligence,
        "waiting": {key: counts.get(key, 0) for key in WAITING},
    }


def _counts(con: duckdb.DuckDBPyConnection, diligence: dict[str, Any] | None) -> dict[str, int]:
    """What waits for the user in the company database (WAITING); the review kinds count the
    rows `fin review list --<kind>` lists. Items of an engagement count only while it is open, and
    so do provision ledgers: the Findings screen, where they are decided, needs an open one."""
    row = con.execute(_COMPANY_WAITING).fetchone()
    assert row is not None
    counts = {
        "ledgers": len(ledgers.read(con, True)),
        "customers": len(customers.read(con, True)),
        "tax": len(tax_ledgers.read(con, True)),
        "netdebt": row[0],
        "reference_lines": row[1],
        "dataroom": len(awaiting_approval(plan_of(con))),
    }
    if diligence is None:
        return counts
    found = con.execute(_ENGAGEMENT_WAITING, [diligence["engagement_id"]]).fetchone()
    assert found is not None
    return counts | {
        "related": found[0],
        "matches": found[1],
        "fdd_drafts": found[2],
        "netdebt_items": found[3],
        "provisions": found[4],
        "queries": sum(diligence["queries"].get(s, 0) for s in QUERY_ACTIONABLE),
        "adjustments": diligence["proposed_adjustments"],
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
            f"  checks not run: {len(s['checks_not_run']) or 'none'}",
            *(f"    {format_not_run(row)}" for row in s["checks_not_run"]),
            f"  figures read by Claude: {s['read_by_claude']}, by the PDF add-on: "
            f"{s['read_by_pdf_addon']}",
            f"  figures stated by the user: {s['stated_figures']}, corrected: "
            f"{s['corrected_figures']} (`fin fact list {c}`)",
            f"  inbox files not ingested: {_list(s['inbox_not_ingested'])}",
            f"  waiting for you: {_waiting_line(s['waiting'])}",
        ]
        out += _format_diligence(s["diligence"])
    return "\n".join(out)


def _waiting_line(counts: dict[str, int]) -> str:
    return "; ".join(f"{n} {WAITING[key]}" for key, n in counts.items() if n) or "nothing"


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
