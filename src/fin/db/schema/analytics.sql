-- shared/analytics.duckdb (PLAN.md §7): every company's metrics in one file for the dashboards.
-- Rebuilt in full by `fin metrics refresh` and after every ingest (fin.metrics.export), so it never holds
-- anything the company databases do not.

-- companies/<company>.duckdb metrics view, one row per company, period, scope and metric.
-- period: 'FY25' for a year, 'FY26-Q1' for a quarter, 'FY26-H1' for a half year, 'FY26-9M' for nine months;
-- period_months: its length (12, 3, 6, 9). Annual rows have period = fiscal_year.
CREATE TABLE metrics (
    company VARCHAR NOT NULL,
    fiscal_year VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    period_months INTEGER NOT NULL,
    period_end DATE,
    scope VARCHAR NOT NULL,
    metric VARCHAR NOT NULL,
    value DECIMAL(38, 6),           -- NULL when an input is missing
    unit VARCHAR NOT NULL,          -- rupees, ratio (0.18 = 18%) or days
    refreshed_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (company, period, scope, metric)
);

-- One row per company: what is loaded and its data quality.
CREATE TABLE companies (
    company VARCHAR PRIMARY KEY,
    documents INTEGER NOT NULL,           -- current documents (status = 'loaded')
    facts BIGINT NOT NULL,                -- facts in current documents
    unconfirmed_facts BIGINT NOT NULL,    -- of those, not confirmed (unmapped or pending review); metrics skip them
    failed_validations BIGINT NOT NULL,   -- failed rows in shared/audit.duckdb validation_results, all time
    last_ingested_at TIMESTAMP,
    refreshed_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL,
    unverified_claude_facts BIGINT,       -- current facts read by Claude (`fin ingest --reading`), not yet verified
    held_documents BIGINT,                -- documents held with open findings (kept out of the metrics)
    open_findings BIGINT,                 -- findings still open
    accepted_findings BIGINT              -- findings accepted, kept as filed
);
