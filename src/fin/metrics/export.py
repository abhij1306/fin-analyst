"""Export every company's metrics, statements and data quality to shared/analytics.duckdb (PLAN.md
Phase 7), for the dashboards.

The metrics are SQL views in each company database (`db/schema/company_views.sql`).
`refresh_analytics` reads them from every `companies/<company>.duckdb`, with the standard statements
(`fin.statements`, in the company's document unit), open and accepted findings, statement coverage,
unread pages and unverified Claude-read facts, then rewrites the analytics tables in one short
transaction, so a dashboard reading analytics.duckdb sees either the old or the new rows and the
file is locked only for that moment. Everything in analytics.duckdb is derived, so a file made by an
older fin (a table missing) is deleted and created again from `analytics.sql`.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fin.audit.errors import ReportRenderFailed
from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import analytics_db_path, audit_db_path, companies_dir
from fin.statements import PER_SHARE, in_unit, statement_rows, unit_title

# Table -> its columns in insert order. Each is emptied and refilled on every refresh.
TABLES: dict[str, list[str]] = {
    "metrics": [
        "company", "fiscal_year", "period", "period_months", "period_end", "scope", "metric",
        "value", "unit", "refreshed_at", "run_id", "category",
    ],
    "companies": [
        "company", "documents", "facts", "unconfirmed_facts", "failed_validations",
        "last_ingested_at", "refreshed_at", "run_id", "unverified_claude_facts", "held_documents",
        "open_findings", "accepted_findings",
    ],
    "metric_definitions": ["metric", "category", "unit", "periods", "definition", "formula"],
    "statements": [
        "company", "scope", "period", "period_months", "period_end", "statement", "line_no",
        "depth", "line_type", "label", "value", "unit",
    ],
    "findings": [
        "company", "finding_id", "status", "document_status", "file_name", "code", "statement",
        "scope", "period", "label", "fin_value", "other_value", "fin_source", "other_source",
        "other_file", "finding",
    ],
    "coverage": [
        "company", "scope", "statement", "figures", "mapped", "mapped_share", "claude_unverified",
    ],
    "unread_pages": ["company", "file_name", "page", "statement", "code", "reason"],
    "unverified_facts": [
        "company", "file_name", "scope", "period", "statement", "label", "account", "value",
        "source",
    ],
}  # fmt: skip


@dataclass(frozen=True)
class Refresh:
    companies: int
    metric_rows: int


_COMPANY_SUMMARY = """
SELECT
    (SELECT count(*) FROM documents WHERE status = 'loaded'),
    count(f.fact_id),
    count(f.fact_id) FILTER (WHERE NOT f.confirmed),
    count(f.fact_id) FILTER (WHERE f.read_by = 'claude' AND NOT f.verified),
    (SELECT max(ingested_at) FROM documents WHERE status = 'loaded'),
    (SELECT count(*) FROM documents WHERE status = 'held'),
    (SELECT count(*) FROM findings WHERE status = 'open'),
    (SELECT count(*) FROM findings WHERE status = 'accepted')
FROM facts f
JOIN documents d ON d.doc_id = f.doc_id AND d.status = 'loaded'
"""

_QUERIES = {
    "metrics": """
        SELECT fiscal_year, period, period_months, period_end, scope, metric, value, unit, category
        FROM metrics ORDER BY scope, period_end, period_months DESC, metric
    """,
    "metric_definitions": """
        SELECT metric, category, unit, periods, definition, formula FROM metric_definitions
    """,
    "findings": """
        SELECT f.finding_id, f.status, d.status, f.file_name, coalesce(f.code, f.check_name),
               f.statement, f.scope, f.period, f.label, f.fin_value, f.other_value, f.fin_source,
               f.other_source, f.other_file, f.finding
        FROM findings f
        LEFT JOIN documents d ON d.doc_id = f.doc_id
        WHERE f.status IN ('open', 'accepted')
        ORDER BY f.finding_id
    """,
    "coverage": """
        SELECT scope, statement, figures, mapped, mapped_share, claude_unverified
        FROM statement_coverage ORDER BY scope, statement
    """,
    "unread_pages": """
        SELECT file_name, unread_pages FROM documents
        WHERE status IN ('loaded', 'held') AND unread_pages IS NOT NULL ORDER BY doc_id
    """,
    "unverified_facts": """
        SELECT d.file_name, f.scope, f.period, r.statement, r.raw_label, f.account, f.value,
               coalesce(r.sheet || '!' || r.cell, 'page ' || r.page)
        FROM facts f
        JOIN documents d ON d.doc_id = f.doc_id
        JOIN raw_values r ON r.raw_id = f.raw_id
        WHERE f.read_by = 'claude' AND NOT f.verified AND d.status IN ('loaded', 'held')
        ORDER BY d.doc_id, f.fact_id
    """,
}


def _statements(root: Path, company: str) -> list[list[Any]]:
    """Every standard statement line, in the company's document unit."""
    try:
        rows = statement_rows(root, company)
    except ReportRenderFailed:  # no current figures (nothing loaded, or every document held)
        return []
    unit = next((r["unit"] for r in rows if r["unit"] != PER_SHARE), "rupees")
    return [
        [
            company, r["scope"], r["period"], r["period_months"], r["period_end"], r["statement"],
            r["line_no"], r["depth"], r["line_type"], r["label"], in_unit(r["value"], r["unit"]),
            "₹ per share" if r["unit"] == PER_SHARE else unit_title(unit),
        ]
        for r in rows
    ]  # fmt: skip


def _ensure_analytics(path: Path) -> None:
    """Create analytics.duckdb, or create it again when it lacks a table (an older fin made it)."""
    if path.exists():
        with connect(path) as con:
            found = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if not set(TABLES) <= found:
            for stale in (path, path.with_name(path.name + ".wal")):
                stale.unlink(missing_ok=True)
    ensure_database(path, "analytics")


def refresh_analytics(run: Run) -> Refresh:
    """Rebuild every analytics table from every company database. A company database made by an
    older fin raises E002."""
    root = run.root
    analytics = analytics_db_path(root)
    _ensure_analytics(analytics)
    now = datetime.now(UTC).replace(tzinfo=None)

    run.flush()  # so this run's validation results are in the audit database
    with connect(audit_db_path(root)) as con:
        failed = dict(
            con.execute(
                "SELECT company, count(*) FROM validation_results WHERE NOT passed GROUP BY company"
            ).fetchall()
        )

    rows: dict[str, list[list[Any]]] = {table: [] for table in TABLES}
    definitions: dict[str, list[Any]] = {}
    for path in sorted(companies_dir(root).glob("*.duckdb")):
        company = path.stem
        ensure_database(path, "company")
        with connect(path) as con:
            fetched = {name: con.execute(sql).fetchall() for name, sql in _QUERIES.items()}
            summary = con.execute(_COMPANY_SUMMARY).fetchone()
        assert summary is not None
        for row in fetched["metrics"]:
            *fields, category = row
            rows["metrics"].append([company, *fields, now, run.run_id, category])
        documents, facts, unconfirmed, unverified_claude, last_ingested, *findings = summary
        rows["companies"].append(
            [company, documents, facts, unconfirmed, failed.get(company, 0), last_ingested]
            + [now, run.run_id, unverified_claude, *findings]
        )
        for row in fetched["metric_definitions"]:
            definitions.setdefault(row[0], list(row))
        for table in ("findings", "coverage", "unverified_facts"):
            rows[table].extend([company, *row] for row in fetched[table])
        for file_name, unread in fetched["unread_pages"]:
            rows["unread_pages"].extend(
                [company, file_name, p["page"], p["statement"], p["code"], p["reason"]]
                for p in json.loads(unread)
            )
        rows["statements"].extend(_statements(root, company))
    rows["metric_definitions"] = list(definitions.values())

    with connect(analytics, write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for table, columns in TABLES.items():
                con.execute(f"DELETE FROM {table}")
                if rows[table]:
                    marks = ", ".join("?" for _ in columns)
                    con.executemany(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})",
                        rows[table],
                    )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    result = Refresh(len(rows["companies"]), len(rows["metrics"]))
    run.event(
        "metrics",
        f"shared/analytics.duckdb: {result.companies} companies, {result.metric_rows} metric rows",
    )
    return result
