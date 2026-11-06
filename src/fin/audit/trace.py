"""`fin audit`: trace a figure back to its source, or list a company's history (PLAN.md §8).

- `fact_trace`: fact -> mapping (dictionary row and review decisions) -> raw value -> file and
  `sheet!cell` or `page N`, plus the ingest run that loaded it.
- `metric_trace`: metric -> facts, then each fact as above. It needs Phase 7's `metric_inputs`.
- `company_history`: ingests, reviews, queries, writes, reports, validation failures and deletions
  from the audit database.

Everything here is read-only. Results are plain dicts, ready for JSON.
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DependencyMissing, SqlFailed
from fin.db.conn import connect
from fin.map.normalize import normalize_label
from fin.paths import audit_db_path, company_db_path, dictionary_db_path


def _rows(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> list[dict[str, Any]]:
    cursor = con.execute(sql, params or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def source_ref(raw: dict[str, Any]) -> str:
    """`sheet!cell` for Excel values, `page N` for PDF values."""
    return f"{raw['sheet']}!{raw['cell']}" if raw["sheet"] else f"page {raw['page']}"


def fact_trace(root: Path, company: str, fact_id: int) -> dict[str, Any]:
    """Trace one fact to its cell or page. Raises E504 if the fact does not exist."""
    with connect(company_db_path(root, company)) as con:
        found = _rows(
            con,
            "SELECT fact_id, account, fiscal_year, period_end, scope, value, confirmed, "
            "mapping_id, raw_id, doc_id FROM facts WHERE fact_id = ?",
            [fact_id],
        )
        if not found:
            raise SqlFailed(f"{company} has no fact with fact_id {fact_id}")
        fact = found[0]
        raw = _rows(
            con,
            "SELECT raw_id, statement, section, raw_label, raw_value_text, raw_unit, "
            "rupees_per_unit, sheet, cell, page, bbox FROM raw_values WHERE raw_id = ?",
            [fact["raw_id"]],
        )[0]
        document = _rows(
            con,
            "SELECT doc_id, file_name, sha256, version, status, archive_path, run_id "
            "FROM documents WHERE doc_id = ?",
            [fact["doc_id"]],
        )[0]
    raw["source"] = source_ref(raw)
    document["archive_exists"] = (root / document["archive_path"]).is_file()
    return {
        "company": company,
        "fact": {key: fact[key] for key in fact if key not in ("raw_id", "doc_id")},
        "raw_value": raw,
        "document": document,
        "mapping": _mapping(root, company, fact["mapping_id"], raw),
        "ingest_run": _ingest_run(root, document["run_id"], f"{company}/{document['file_name']}"),
    }


def _mapping(
    root: Path, company: str, mapping_id: int | None, raw: dict[str, Any]
) -> dict[str, Any]:
    """The dictionary row the fact points at, and every review decision about its label."""
    dictionary = None
    if mapping_id is not None:
        with connect(dictionary_db_path(root)) as con:
            rows = _rows(
                con,
                "SELECT mapping_id, label, section, account, scope, company, source, created_at "
                "FROM label_map WHERE mapping_id = ?",
                [mapping_id],
            )
        dictionary = rows[0] if rows else None
    with connect(audit_db_path(root)) as con:
        decisions = _rows(
            con,
            "SELECT run_id, recorded_at, section, raw_label, account, scope, decision, actor, "
            "on_request, confidence, reason FROM mapping_decisions "
            "WHERE company = ? AND statement = ? ORDER BY recorded_at",
            [company, raw["statement"]],
        )
    label, section = normalize_label(raw["raw_label"]), normalize_label(raw["section"])
    decisions = [
        d
        for d in decisions
        if normalize_label(d["raw_label"]) == label
        and normalize_label(d["section"] or "") == section
    ]
    return {"dictionary": dictionary, "decisions": decisions}


def _ingest_run(root: Path, run_id: str, event_file: str) -> dict[str, Any]:
    with connect(audit_db_path(root)) as con:
        runs = _rows(
            con,
            "SELECT s.run_id, s.command, s.started_at, e.status, e.exit_code "
            "FROM runs s LEFT JOIN runs e ON e.run_id = s.run_id AND e.record = 'end' "
            "WHERE s.record = 'start' AND s.run_id = ?",
            [run_id],
        )
        events = _rows(
            con,
            "SELECT seq, stage, level, error_code, message FROM events "
            "WHERE run_id = ? AND file = ? ORDER BY seq",
            [run_id, event_file],
        )
    run = runs[0] if runs else {"run_id": run_id, "note": "run not in the audit database"}
    return run | {"events": events}


def metric_trace(root: Path, company: str, metric: str, fiscal_year: str) -> dict[str, Any]:
    """Trace a metric to its facts. Needs `metric_inputs` from Phase 7, which is not built yet."""
    raise DependencyMissing(
        f"metrics not available yet: cannot trace {metric} {fiscal_year} for {company}. "
        "Trace a single figure with `fin audit <company> --fact <fact_id>`"
    )


def company_history(root: Path, company: str) -> dict[str, Any]:
    """Everything the audit database holds about a company, oldest first."""
    ingest_runs: set[str] = set()
    if company_db_path(root, company).is_file():
        with connect(company_db_path(root, company)) as con:
            ingest_runs = {
                r[0] for r in con.execute("SELECT DISTINCT run_id FROM documents").fetchall()
            }
    with connect(audit_db_path(root)) as con:
        runs = _rows(
            con,
            "SELECT s.run_id, s.command, s.args, s.started_at, e.status, e.exit_code, "
            "e.loaded, e.skipped, e.failed "
            "FROM runs s LEFT JOIN runs e ON e.run_id = s.run_id AND e.record = 'end' "
            "WHERE s.record = 'start' AND s.command <> 'audit' ORDER BY s.started_at",
        )
        runs = [
            r
            for r in runs
            if json.loads(r["args"]).get("company") == company or r["run_id"] in ingest_runs
        ]
        problems = _rows(
            con,
            "SELECT run_id, stage, level, error_code, file, message FROM events "
            "WHERE level IN ('warning', 'error') ORDER BY run_id, seq",
        )
        by_company = "WHERE company = ? ORDER BY recorded_at"
        return {
            "company": company,
            "runs": [
                r | {"problems": [p for p in problems if p["run_id"] == r["run_id"]]} for r in runs
            ],
            "mapping_decisions": _rows(
                con, f"SELECT * FROM mapping_decisions {by_company}", [company]
            ),
            "queries": _rows(con, f"SELECT * FROM queries {by_company}", [company]),
            "reports": _rows(con, f"SELECT * FROM reports {by_company}", [company]),
            "validation_failures": _rows(
                con,
                "SELECT * FROM validation_results WHERE company = ? AND NOT passed "
                "ORDER BY recorded_at",
                [company],
            ),
            "deletions": _rows(con, f"SELECT * FROM deletions {by_company}", [company]),
        }


def format_fact_trace(trace: dict[str, Any]) -> str:
    fact, raw, doc = trace["fact"], trace["raw_value"], trace["document"]
    run, mapping = trace["ingest_run"], trace["mapping"]
    lines = [
        f"fact {fact['fact_id']}: {fact['account'] or '(unmapped)'} {fact['fiscal_year']} "
        f"{fact['scope']} = {fact['value']} rupees (mapping confirmed: {fact['confirmed']})",
        f"raw value: {raw['raw_label']!r} = {raw['raw_value_text']} {raw['raw_unit']} "
        f"under {raw['section']!r} ({raw['statement']})",
        f"source: {doc['file_name']} {raw['source']} (version {doc['version']}, {doc['status']})",
        f"archived copy: {doc['archive_path']}" + ("" if doc["archive_exists"] else " (MISSING)"),
        f"ingest run: {run['run_id']} {run.get('command', '')} {run.get('status', '')}",
    ]
    lines += [f"  {e['level']} {e['error_code'] or ''} {e['message']}" for e in run["events"]]
    label_row = mapping["dictionary"]
    if label_row:
        lines.append(
            f"dictionary mapping {label_row['mapping_id']}: {label_row['label']!r} -> "
            f"{label_row['account']} ({label_row['scope']}, {label_row['source']})"
        )
    lines += [
        f"decision: {d['recorded_at']} {d['decision']} by {d['actor']} "
        f"(run {d['run_id']}) account {d['account']}: {d['reason'] or ''}"
        for d in mapping["decisions"]
    ]
    return "\n".join(lines)


HISTORY_SECTIONS = [
    (
        "mapping_decisions",
        "mapping decisions",
        "{recorded_at} {decision} {raw_label!r} -> {account} by {actor}",
    ),
    ("queries", "queries and writes", "{recorded_at} {mode} {query_id} {question} {sql}"),
    ("reports", "reports", "{recorded_at} {output_paths}"),
    (
        "validation_failures",
        "validation failures",
        "{recorded_at} {error_code} {check_name} {message}",
    ),
    ("deletions", "deletions", "{recorded_at} removed {files_removed}, backup {backup_path}"),
]


def format_history(history: dict[str, Any]) -> str:
    lines = [f"history of {history['company']}", "", "runs:"]
    for run in history["runs"]:
        lines.append(
            f"  {run['started_at']} {run['run_id']} {run['command']} -> "
            f"{run['status'] or 'did not finish'} (loaded {run['loaded']}, failed {run['failed']})"
        )
        lines += [
            f"      {p['level']} {p['error_code'] or ''} {p['file'] or ''} {p['message']}"
            for p in run["problems"]
        ]
    for key, title, template in HISTORY_SECTIONS:
        lines += ["", f"{title}:"]
        lines += [f"  {template.format(**row)}" for row in history[key]] or ["  none"]
    return "\n".join(lines)
