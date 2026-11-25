"""What an eval counts beside the golden checks: where fin's reading of a company needs work.

- `error_codes`: events with an error code in the audit log, for runs naming the company;
- `findings`: open and accepted findings by code (the check name when there is no code);
- `unread_pages`: statement pages of loaded and held documents fin could not read, by code;
- `read_by_claude`: figures from a Claude reading in loaded and held documents, by trust status:
  the document loaded or held, and the latest `fin verify` line on the figure (agree, differ, or not
  verified). Whether one is trusted stays the user's decision; this only counts;
- `read_by_pdf_addon`: figures the PDF add-on read;
- `unmapped_labels`: distinct printed lines with no confirmed account, and mapping items waiting;
- `ledgers`: trial balance ledgers by mapping status (only confirmed ones count in reconciliations);
- `bank_statements`: loaded bank statements by reader (excel or the PDF add-on), and whether they
  tie;
- `queries`: the latest engagement's queries by status, and the approved-or-later ones that group
  reconciliation lines (the issues an export marks up);
- `passthrough`: customers on the latest engagement's pass-through list, and its adjustments by
  status;
- `customers`: customers (tokens of the sales registers) by sector status: pending, proposed,
  confirmed or rejected (only confirmed sectors count in the sector mix);
- `recon_lines`: the latest D4 reconciliation runs' detail lines by reconciliation and kind, and
  how many need a query but are in none.
- `registers`: lines of the loaded registers file by kind (sales, purchase);
- `payroll`: loaded payroll files by the unit the user confirmed (or "not confirmed").
- `bank_matches`: lines of the latest `bank_vs_invoices` runs by kind and match pass, and the match
  proposals by status.
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from fin.db.conn import connect
from fin.evals.golden import (
    ANALYTICS_KINDS,
    EXPORTED,
    LATEST_ENGAGEMENT,
    RECAST_KINDS,
    RECON_KINDS,
    Check,
)
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


_BANK = f"""
SELECT b.reader, b.ties, count(*) FROM bank_statements b JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT} GROUP BY ALL ORDER BY ALL
"""


_REGISTERS = f"""
SELECT r.kind, count(*) FROM register_lines r JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT} GROUP BY ALL ORDER BY ALL
"""
_PAYROLL = f"""
SELECT coalesce(u.unit, 'not confirmed'), count(*) FROM source_files s
LEFT JOIN payroll_units u USING (file_id)
WHERE s.kind = 'payroll' AND s.status = 'loaded' AND s.engagement_id = {LATEST_ENGAGEMENT}
GROUP BY ALL ORDER BY ALL
"""
_MATCHES = f"""
SELECT i.kind, coalesce(i.match_pass, '-'), count(*)
FROM recon_items i JOIN recon_runs r USING (recon_id)
WHERE r.name = 'bank_vs_invoices' AND r.engagement_id = {LATEST_ENGAGEMENT}
    AND r.recon_id IN (
        SELECT max(recon_id) FROM recon_runs WHERE name = 'bank_vs_invoices' GROUP BY period
    )
GROUP BY ALL ORDER BY ALL
"""
MATCH_KINDS = ("bank_unmatched", "invoice_unmatched", "fx_line")


def _bank_matches(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    lines = {f"{kind}, {how}": n for kind, how, n in con.execute(_MATCHES).fetchall()}
    proposals = dict(
        con.execute("SELECT status, count(*) FROM match_proposals GROUP BY 1 ORDER BY 1").fetchall()
    )
    return {"lines": lines, "proposals": proposals}


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


def _recon_lines(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    names = ", ".join(f"'{k}'" for k in RECON_KINDS)
    out: dict[str, Any] = {}
    for name, kind, needing, n in con.execute(
        "SELECT r.name, i.kind, count(*) FILTER (WHERE i.needs_query AND i.query_id IS NULL), "
        "count(*) FROM recon_items i JOIN recon_runs r USING (recon_id) WHERE r.recon_id IN "
        f"(SELECT max(recon_id) FROM recon_runs WHERE engagement_id = {LATEST_ENGAGEMENT} "
        f"GROUP BY name, period) AND r.name IN ({names}) AND i.level IN ('invoice', 'ledger', "
        "'payroll') GROUP BY ALL ORDER BY ALL"
    ).fetchall():
        entry = out.setdefault(name, {"kinds": {}, "not_in_a_query": 0})
        entry["kinds"][kind] = entry["kinds"].get(kind, 0) + n
        entry["not_in_a_query"] += needing
    return out


def _queries(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    by_status = dict(
        con.execute(
            "SELECT status, count(*) FROM accountant_queries "
            f"WHERE engagement_id = {LATEST_ENGAGEMENT} GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    return {
        "by_status": by_status,
        "issues_with_lines": _one(
            con,
            "SELECT count(DISTINCT q.query_id) FROM query_items q JOIN accountant_queries a "
            f"ON a.query_id = q.query_id WHERE a.engagement_id = {LATEST_ENGAGEMENT} "
            f"AND {EXPORTED}",
        ),
    }


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
        "recon_lines": _recon_lines(con),
        "queries": _queries(con),
        "error_codes": _audit_codes(root, company),
        "findings": findings,
        "unread_pages": _unread(con),
        "read_by_claude": claude,
        "read_by_pdf_addon": _one(con, _ADDON),
        "unmapped_labels": _one(con, _UNMAPPED),
        "mappings_waiting": len(open_items(read_pending(root, company))),
        "ledgers": ledgers,
        "bank_matches": _bank_matches(con),
        "customers": dict(
            con.execute("SELECT status, count(*) FROM customers GROUP BY 1 ORDER BY 1").fetchall()
        ),
        "bank_statements": {
            f"{reader}, {'ties' if ties else 'does not tie'}": n
            for reader, ties, n in con.execute(_BANK).fetchall()
        },
        "recast": {
            "undated_lines": _one(
                con,
                "SELECT count(*) FROM recon_items i JOIN recon_runs r USING (recon_id) "
                "WHERE i.kind = 'undated' AND r.recon_id IN (SELECT max(recon_id) FROM recon_runs "
                f"WHERE name = 'service_recast' AND engagement_id = {LATEST_ENGAGEMENT} "
                "GROUP BY period)",
            ),
            "adjustments": dict(
                con.execute(
                    "SELECT status, count(*) FROM adjustments WHERE category = 'recast' "
                    f"AND engagement_id = {LATEST_ENGAGEMENT} GROUP BY 1 ORDER BY 1"
                ).fetchall()
            ),
        },
        "passthrough": {
            "customers": _one(
                con,
                "SELECT count(*) FROM passthrough_customers WHERE engagement_id = "
                f"{LATEST_ENGAGEMENT}",
            ),
            "adjustments": dict(
                con.execute(
                    "SELECT status, count(*) FROM adjustments WHERE engagement_id = "
                    f"{LATEST_ENGAGEMENT} GROUP BY 1 ORDER BY 1"
                ).fetchall()
            ),
        },
        "registers": dict(con.execute(_REGISTERS).fetchall()),
        "payroll": dict(con.execute(_PAYROLL).fetchall()),
    }


def _statement_codes(con: duckdb.DuckDBPyConnection, check: Check) -> list[str]:
    """Codes of the findings on a statement figure's period and scope, and `held`."""
    codes: set[str] = {
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


def related_codes(con: duckdb.DuckDBPyConnection, check: Check) -> list[str]:
    """What may explain a check that is not passing: the codes of open and accepted findings on the
    same period (and scope), and markers: `held` (a held document has the figure), `not_reconciled`
    (no `tb_vs_statements` run for the period), `unmapped_ledgers` (its latest run had some),
    `no_passthrough_list` (a revenue split or analytics check with no pass-through customers
    recorded), `unconfirmed_sectors` (a sector check while customers have no confirmed sector),
    `recast_not_run` (a recast check for a period with no `fin recast run`)."""
    codes: set[str] = set()
    if check.kind == "statement":
        return _statement_codes(con, check)
    if check.kind in (*MATCH_KINDS, "bank_vs_tb"):
        name = "bank_vs_tb" if check.kind == "bank_vs_tb" else "bank_vs_invoices"
        ran = con.execute(
            "SELECT count(*) FROM recon_runs WHERE name = ? AND period = ? "
            f"AND engagement_id = {LATEST_ENGAGEMENT}",
            [name, check.period],
        ).fetchone()
        if ran is not None and not ran[0]:
            codes.add("not_reconciled")
        waiting = con.execute(
            "SELECT count(*) FROM match_proposals WHERE status = 'proposed'"
        ).fetchone()
        if check.kind in MATCH_KINDS and waiting is not None and waiting[0]:
            codes.add("match_proposals_waiting")
    if check.kind == "bank_closing":  # the period is a date: findings on that account's statements
        return sorted(
            c
            for (c,) in con.execute(
                "SELECT DISTINCT f.check_name FROM findings f "
                "JOIN bank_statements b USING (file_id) "
                "WHERE f.status IN ('open', 'accepted') AND b.account = ?",
                [check.ledger],
            ).fetchall()
        )
    codes |= {
        c
        for (c,) in con.execute(
            "SELECT DISTINCT coalesce(f.code, f.check_name) FROM findings f JOIN source_files s "
            "USING (file_id) WHERE f.status IN ('open', 'accepted') AND s.period = ? "
            f"AND s.engagement_id = {LATEST_ENGAGEMENT}",
            [check.period],
        ).fetchall()
    }
    if check.kind == "sector_revenue":
        waiting = con.execute(
            "SELECT count(*) FROM customers WHERE status <> 'confirmed'"
        ).fetchone()
        if waiting and waiting[0]:
            codes.add("unconfirmed_sectors")
    if check.kind in ("core_revenue", "passthrough_revenue", *ANALYTICS_KINDS):
        listed = con.execute(
            f"SELECT count(*) FROM passthrough_customers WHERE engagement_id = {LATEST_ENGAGEMENT}"
        ).fetchone()
        if listed and not listed[0]:
            codes.add("no_passthrough_list")
    if check.kind in RECAST_KINDS:
        ran = con.execute(
            "SELECT count(*) FROM recon_runs WHERE period = ? AND name = 'service_recast' "
            f"AND engagement_id = {LATEST_ENGAGEMENT}",
            [check.period],
        ).fetchone()
        if ran is not None and not ran[0]:
            codes.add("recast_not_run")
    if check.kind == "tb_vs_statements" or check.kind in RECON_KINDS:
        run = con.execute(
            "SELECT unmapped FROM recon_runs WHERE period = ? AND name = ? "
            f"AND engagement_id = {LATEST_ENGAGEMENT} ORDER BY recon_id DESC LIMIT 1",
            [check.period, check.kind],
        ).fetchone()
        if run is None:
            codes.add("not_reconciled")
        elif run[0]:
            codes.add("unmapped_ledgers")
    return sorted(codes)
