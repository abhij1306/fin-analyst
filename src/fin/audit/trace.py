"""`fin audit`: trace a figure back to its source, or list a company's history (PLAN.md §8).

- `fact_trace`: fact -> mapping (dictionary row and review decisions) -> raw value -> file and
  `sheet!cell` or `page N`, plus the ingest run that loaded it.
- `metric_trace`: metric -> facts (`metric_inputs`), then each fact as above.
- `company_history`: ingests, reviews, queries, writes, reports, validation failures and deletions
  from the audit database.

Everything here is read-only. Results are plain dicts, ready for JSON.
"""

import json
from pathlib import Path
from typing import Any

from fin.audit.errors import SqlFailed
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.map.dictionary import load_accounts
from fin.map.normalize import normalize_label
from fin.paths import audit_db_path, company_db_path, dictionary_db_path


def fact_trace(root: Path, company: str, fact_id: int) -> dict[str, Any]:
    """Trace one fact to its cell or page. Raises E504 if the fact does not exist."""
    with connect(company_db_path(root, company)) as con:
        found = fetch_dicts(
            con,
            "SELECT fact_id, account, fiscal_year, period, period_end, scope, value, confirmed, "
            "mapping_id, raw_id, doc_id FROM facts WHERE fact_id = ?",
            [fact_id],
        )
        if not found:
            raise SqlFailed(f"{company} has no fact with fact_id {fact_id}")
        fact = found[0]
        raw = fetch_dicts(
            con,
            "SELECT raw_id, statement, section, raw_label, raw_value_text, raw_unit, "
            "rupees_per_unit, sheet, cell, page, bbox, source_ref(sheet, cell, page) AS source "
            "FROM raw_values WHERE raw_id = ?",
            [fact["raw_id"]],
        )[0]
        document = fetch_dicts(
            con,
            "SELECT doc_id, file_name, sha256, version, status, archive_path, run_id "
            "FROM documents WHERE doc_id = ?",
            [fact["doc_id"]],
        )[0]
    document["archive_exists"] = (root / document["archive_path"]).is_file()
    # The account's unit: rupees, or rupees_per_share (EPS, stored as printed). Unmapped: rupees.
    unit = load_accounts(root)[fact["account"]].unit if fact["account"] else "rupees"
    return {
        "company": company,
        "fact": {key: fact[key] for key in fact if key not in ("raw_id", "doc_id")}
        | {"unit": unit},
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
            rows = fetch_dicts(
                con,
                "SELECT mapping_id, label, section, account, scope, company, source, created_at "
                "FROM label_map WHERE mapping_id = ?",
                [mapping_id],
            )
        dictionary = rows[0] if rows else None
    with connect(audit_db_path(root)) as con:
        decisions = fetch_dicts(
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
        runs = fetch_dicts(
            con,
            "SELECT s.run_id, s.command, s.started_at, e.status, e.exit_code "
            "FROM runs s LEFT JOIN runs e ON e.run_id = s.run_id AND e.record = 'end' "
            "WHERE s.record = 'start' AND s.run_id = ?",
            [run_id],
        )
        events = fetch_dicts(
            con,
            "SELECT seq, stage, level, error_code, message FROM events "
            "WHERE run_id = ? AND file = ? ORDER BY seq",
            [run_id, event_file],
        )
    run = runs[0] if runs else {"run_id": run_id, "note": "run not in the audit database"}
    return run | {"events": events}


def metric_trace(root: Path, company: str, metric: str, fiscal_year: str) -> dict[str, Any]:
    """Trace a metric to the facts it used (`metric_inputs`), and each fact to its source. One entry
    per scope. An input with no fact (fact_id null) is why the metric is null. `fiscal_year` is a
    period: a fiscal year ("FY25") or a quarter or half year ("FY26-Q1"). Raises E504 if the
    company has no such metric and period."""
    with connect(company_db_path(root, company)) as con:
        values = fetch_dicts(
            con,
            "SELECT scope, value, unit FROM metrics WHERE metric = ? AND period = ? ORDER BY scope",
            [metric, fiscal_year],
        )
        inputs = fetch_dicts(
            con,
            "SELECT scope, account, stands_in_for, input_period AS input_fiscal_year, fact_id "
            "FROM metric_inputs WHERE metric = ? AND period = ? "
            "ORDER BY scope, stands_in_for NULLS FIRST, account, fact_id",
            [metric, fiscal_year],
        )
    if not values:
        raise SqlFailed(f"{company} has no metric {metric!r} for {fiscal_year}")
    scopes = []
    for entry in values:
        rows = [
            {key: row[key] for key in ("account", "stands_in_for", "input_fiscal_year", "fact_id")}
            # fact_ids start at 1, so a missing input (NULL) is the only one without a trace.
            | {"trace": row["fact_id"] and fact_trace(root, company, row["fact_id"])}
            for row in inputs
            if row["scope"] == entry["scope"]
        ]
        scopes.append(entry | {"inputs": rows})
    return {"company": company, "metric": metric, "fiscal_year": fiscal_year, "scopes": scopes}


def format_metric_trace(trace: dict[str, Any]) -> str:
    lines = []
    for scope in trace["scopes"]:
        lines.append(
            f"{trace['metric']} {trace['fiscal_year']} {scope['scope']} = {scope['value']} "
            f"({scope['unit']})"
        )
        for item in scope["inputs"]:
            if item["trace"] is None:
                lines.append(f"  {item['account']} {item['input_fiscal_year']}: MISSING (no fact)")
                continue
            traced = item["trace"]
            fact, raw, doc = traced["fact"], traced["raw_value"], traced["document"]
            via = f" (stands in for {item['stands_in_for']})" if item["stands_in_for"] else ""
            lines.append(
                f"  {item['account']}{via} {item['input_fiscal_year']}: {fact['value']} "
                f"= fact {fact['fact_id']}, {doc['file_name']} {raw['source']}, "
                f"mapping confirmed: {fact['confirmed']}"
            )
    return "\n".join(lines)


# Runs whose audit rows name the company, whatever their args (`fin ingest-batch` names only a
# folder). Events about a company's documents have file `<company>/<file name>`.
_TAGGED_RUNS_SQL = """
SELECT run_id FROM events WHERE starts_with(file, $prefix)
UNION SELECT run_id FROM mapping_decisions WHERE company = $company
UNION SELECT run_id FROM validation_results WHERE company = $company
UNION SELECT run_id FROM findings WHERE company = $company
UNION SELECT run_id FROM queries WHERE company = $company
UNION SELECT run_id FROM reports WHERE company = $company
UNION SELECT run_id FROM deletions WHERE company = $company
UNION SELECT run_id FROM diligence_events WHERE company = $company
"""


def company_history(root: Path, company: str) -> dict[str, Any]:
    """Everything the audit database holds about a company, oldest first. A run belongs to the
    company when its args name it or an audit row of it does (so a batch ingest stays in the history
    after the company is deleted). A run that did not name the company shows only the problems with
    the company's files."""
    prefix = f"{company}/"
    with connect(audit_db_path(root)) as con:
        tagged = {
            r[0]
            for r in con.execute(
                _TAGGED_RUNS_SQL, {"prefix": prefix, "company": company}
            ).fetchall()
        }
        runs = fetch_dicts(
            con,
            "SELECT s.run_id, s.command, s.args, s.started_at, e.status, e.exit_code, "
            "e.loaded, e.skipped, e.failed "
            "FROM runs s LEFT JOIN runs e ON e.run_id = s.run_id AND e.record = 'end' "
            "WHERE s.record = 'start' AND s.command <> 'audit' ORDER BY s.started_at",
        )
        named = {r["run_id"] for r in runs if json.loads(r["args"]).get("company") == company}
        runs = [r for r in runs if r["run_id"] in named or r["run_id"] in tagged]
        problems: dict[str, list[dict[str, Any]]] = {}
        for p in fetch_dicts(
            con,
            "SELECT run_id, stage, level, error_code, file, message FROM events "
            "WHERE level IN ('warning', 'error') ORDER BY run_id, seq",
        ):
            if p["run_id"] in named or (p["file"] or "").startswith(prefix):
                problems.setdefault(p["run_id"], []).append(p)
        by_company = "WHERE company = ? ORDER BY recorded_at"
        return {
            "company": company,
            "runs": [r | {"problems": problems.get(r["run_id"], [])} for r in runs],
            "mapping_decisions": fetch_dicts(
                con, f"SELECT * FROM mapping_decisions {by_company}", [company]
            ),
            "queries": fetch_dicts(con, f"SELECT * FROM queries {by_company}", [company]),
            "reports": fetch_dicts(con, f"SELECT * FROM reports {by_company}", [company]),
            "validation_failures": fetch_dicts(
                con,
                "SELECT * FROM validation_results WHERE company = ? AND NOT passed "
                "ORDER BY recorded_at",
                [company],
            ),
            "deletions": fetch_dicts(con, f"SELECT * FROM deletions {by_company}", [company]),
        }


def format_fact_trace(trace: dict[str, Any]) -> str:
    fact, raw, doc = trace["fact"], trace["raw_value"], trace["document"]
    run, mapping = trace["ingest_run"], trace["mapping"]
    lines = [
        f"fact {fact['fact_id']}: {fact['account'] or '(unmapped)'} {fact['period']} "
        f"{fact['scope']} = {fact['value']} {fact['unit'].replace('_', ' ')} "
        f"(mapping confirmed: {fact['confirmed']})",
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
