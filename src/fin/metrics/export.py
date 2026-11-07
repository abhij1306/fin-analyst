"""Export every company's metrics to shared/analytics.duckdb (PLAN.md Phase 7).

The metrics are SQL views in each company database (`db/schema/company_views.sql`).
`refresh_analytics` reads them from every `companies/<company>.duckdb`, then rewrites the analytics
tables in one short transaction, so a dashboard reading analytics.duckdb sees either the old or the
new rows and the file is locked only for that moment.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.paths import analytics_db_path, audit_db_path, companies_dir


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


def refresh_analytics(run: Run) -> Refresh:
    """Rebuild analytics.metrics and analytics.companies from every company database. A company
    database made by an older fin raises E002."""
    root = run.root
    analytics = analytics_db_path(root)
    ensure_database(analytics, "analytics")
    now = datetime.now(UTC).replace(tzinfo=None)

    run.flush()  # so this run's validation results are in the audit database
    with connect(audit_db_path(root)) as con:
        failed = dict(
            con.execute(
                "SELECT company, count(*) FROM validation_results WHERE NOT passed GROUP BY company"
            ).fetchall()
        )

    metrics: list[list[Any]] = []
    companies: list[list[Any]] = []
    for path in sorted(companies_dir(root).glob("*.duckdb")):
        company = path.stem
        ensure_database(path, "company")
        with connect(path) as con:
            rows = con.execute(
                "SELECT fiscal_year, period, period_months, period_end, scope, metric, value, unit "
                "FROM metrics ORDER BY scope, period_end, period_months DESC, metric"
            ).fetchall()
            summary = con.execute(_COMPANY_SUMMARY).fetchone()
        assert summary is not None
        metrics.extend([company, *row, now, run.run_id] for row in rows)
        documents, facts, unconfirmed, unverified_claude, last_ingested, *findings = summary
        companies.append(
            [company, documents, facts, unconfirmed, failed.get(company, 0), last_ingested]
            + [now, run.run_id, unverified_claude, *findings]
        )

    with connect(analytics, write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM metrics")
            con.execute("DELETE FROM companies")
            if metrics:
                con.executemany(
                    "INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", metrics
                )
            if companies:
                con.executemany(
                    "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", companies
                )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    result = Refresh(len(companies), len(metrics))
    run.event(
        "metrics",
        f"shared/analytics.duckdb: {result.companies} companies, {result.metric_rows} metric rows",
    )
    return result
