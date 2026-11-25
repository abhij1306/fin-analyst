"""What an eval counts beside the golden checks: where fin's reading of a company needs work.

- `error_codes`: events with an error code in the audit log, for runs naming the company;
- `findings`: open and accepted findings by code (the check name when there is no code);
- `unread_pages`: statement pages of loaded and held documents fin could not read, by code;
- `read_by_claude`: figures from a Claude reading in loaded and held documents, by trust status:
  the document loaded or held, and the latest `fin verify` line on the figure (agree, differ, or not
  verified). Whether one is trusted stays the user's decision; this only counts;
- `read_by_pdf_addon`: figures the PDF add-on read;
- `unmapped_labels`: distinct printed lines with no confirmed account, and mapping items waiting;
- `ledgers`: trial balance ledgers by mapping status (only confirmed ones count in reconciliations).
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from fin.db.conn import connect
from fin.evals.golden import Check
from fin.map.review import open_items, read_pending
from fin.paths import audit_db_path

LIVE = "('loaded', 'held')"

_ERRORS = """
SELECT e.error_code, count(*) FROM events e
WHERE e.error_code IS NOT NULL AND e.run_id IN (
    SELECT run_id FROM runs WHERE record = 'start' AND json_extract_string(args, '$.company') = ?
)
GROUP BY 1 ORDER BY 1
"""
_FINDINGS = """
SELECT coalesce(code, check_name), status, count(*) FROM findings
WHERE status IN ('open', 'accepted') GROUP BY ALL ORDER BY ALL
"""
_CLAUDE = f"""
SELECT d.status, coalesce((
    SELECT v.outcome FROM verify_lines v JOIN verify_runs r USING (run_id)
    WHERE v.fact_id = f.fact_id ORDER BY r.verified_at DESC LIMIT 1
), 'not verified'), count(*)
FROM facts f JOIN documents d USING (doc_id)
WHERE f.read_by = 'claude' AND d.status IN {LIVE}
GROUP BY ALL ORDER BY ALL
"""
_ADDON = f"""
SELECT count(*) FROM facts f JOIN documents d USING (doc_id)
WHERE f.read_by NOT IN ('fin', 'claude') AND d.status IN {LIVE}
"""
_UNMAPPED = f"""
SELECT count(DISTINCT (r.statement, r.section, r.raw_label)) FROM facts f
JOIN raw_values r USING (raw_id) JOIN documents d ON d.doc_id = f.doc_id
WHERE (f.account IS NULL OR NOT f.confirmed) AND d.status IN {LIVE}
"""


def _audit_codes(root: Path, company: str) -> dict[str, int]:
    path = audit_db_path(root)
    if not path.is_file():
        return {}
    with connect(path) as con:
        return dict(con.execute(_ERRORS, [company]).fetchall())


def _unread(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    pages: dict[str, int] = {}
    for (text,) in con.execute(
        f"SELECT unread_pages FROM documents WHERE status IN {LIVE} AND unread_pages IS NOT NULL"
    ).fetchall():
        for page in json.loads(text):
            code = str(page.get("code") or "none")
            pages[code] = pages.get(code, 0) + 1
    return {"pages": sum(pages.values()), "by_code": pages}


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def counts(root: Path, company: str, con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    findings: dict[str, dict[str, int]] = {}
    for code, status, n in con.execute(_FINDINGS).fetchall():
        findings.setdefault(code, {})[status] = n
    claude: dict[str, Any] = {"figures": 0, "document": {}, "verify": {}}
    for status, outcome, n in con.execute(_CLAUDE).fetchall():
        claude["figures"] += n
        claude["document"][status] = claude["document"].get(status, 0) + n
        claude["verify"][outcome] = claude["verify"].get(outcome, 0) + n
    ledgers = dict(
        con.execute("SELECT status, count(*) FROM ledger_accounts GROUP BY 1 ORDER BY 1").fetchall()
    )
    return {
        "error_codes": _audit_codes(root, company),
        "findings": findings,
        "unread_pages": _unread(con),
        "read_by_claude": claude,
        "read_by_pdf_addon": _one(con, _ADDON),
        "unmapped_labels": _one(con, _UNMAPPED),
        "mappings_waiting": len(open_items(read_pending(root, company))),
        "ledgers": ledgers,
    }


def related_codes(con: duckdb.DuckDBPyConnection, check: Check) -> list[str]:
    """What may explain a check that is not passing: the codes of open and accepted findings on the
    same period (and scope), and markers: `held` (a held document has the figure), `not_reconciled`
    (no `tb_vs_statements` run for the period), `unmapped_ledgers` (its latest run had some)."""
    codes: set[str] = set()
    if check.kind == "statement":
        codes |= {
            c
            for (c,) in con.execute(
                "SELECT DISTINCT coalesce(code, check_name) FROM findings WHERE status IN "
                "('open', 'accepted') AND period = ? AND scope = ?",
                [check.period, check.scope],
            ).fetchall()
        }
        held = con.execute(
            "SELECT count(*) FROM document_facts x JOIN documents d USING (doc_id) WHERE "
            "d.status = 'held' AND x.period = ? AND x.scope = ? AND x.account = ?",
            [check.period, check.scope, check.account],
        ).fetchone()
        if held and held[0]:
            codes.add("held")
        return sorted(codes)
    codes |= {
        c
        for (c,) in con.execute(
            "SELECT DISTINCT coalesce(f.code, f.check_name) FROM findings f JOIN source_files s "
            "USING (file_id) WHERE f.status IN ('open', 'accepted') AND s.period = ?",
            [check.period],
        ).fetchall()
    }
    if check.kind == "tb_vs_statements":
        run = con.execute(
            "SELECT unmapped FROM recon_runs WHERE period = ? ORDER BY recon_id DESC LIMIT 1",
            [check.period],
        ).fetchone()
        if run is None:
            codes.add("not_reconciled")
        elif run[0]:
            codes.add("unmapped_ledgers")
    return sorted(codes)
