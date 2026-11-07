-- shared/analytics.duckdb (PLAN.md §7): every company's metrics in one file for the dashboards.
-- Rebuilt in full after every ingest and data change (fin.metrics.export), so it never holds
-- anything the company databases do not. A file made by an older fin (different tables or columns) is deleted and
-- created again at the next refresh: everything in it is derived.

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
    category VARCHAR,               -- metric_definitions.category: profitability, returns, liquidity, ...
    PRIMARY KEY (company, period, scope, metric)
);

-- The company metric_definitions view: one row per metric.
CREATE TABLE metric_definitions (
    metric VARCHAR PRIMARY KEY,
    category VARCHAR NOT NULL,
    unit VARCHAR NOT NULL,
    periods VARCHAR NOT NULL,       -- annual, all, interim or quarter
    definition VARCHAR NOT NULL,
    formula VARCHAR NOT NULL
);

-- Standard statements (fin.statements): every line of every period and scope, in the unit the company's documents
-- use (unit, e.g. '₹ in lakh'; per-share lines as printed, unit '₹ per share'). line_type 'other' is the
-- "Unmapped / other" line under a subtotal its mapped lines do not fully explain.
CREATE TABLE statements (
    company VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    period_months INTEGER NOT NULL,
    period_end DATE,
    statement VARCHAR NOT NULL,     -- profit_and_loss, balance_sheet, cash_flow
    line_no INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    line_type VARCHAR NOT NULL,     -- line or other
    label VARCHAR NOT NULL,
    value DECIMAL(38, 2),
    unit VARCHAR NOT NULL
);

-- Findings open or accepted (company findings table), with the document's status. Values in rupees.
CREATE TABLE findings (
    company VARCHAR NOT NULL,
    finding_id BIGINT NOT NULL,
    status VARCHAR NOT NULL,        -- open or accepted
    document_status VARCHAR,        -- loaded or held
    file_name VARCHAR,
    code VARCHAR,                   -- §9 code, or the check name when it has none
    statement VARCHAR,
    scope VARCHAR,
    period VARCHAR,
    label VARCHAR,
    fin_value DECIMAL(20, 2),
    other_value DECIMAL(20, 2),
    fin_source VARCHAR,
    other_source VARCHAR,
    other_file VARCHAR,
    finding VARCHAR NOT NULL
);

-- The company statement_coverage view: mapped share of each statement's figures in loaded documents.
CREATE TABLE coverage (
    company VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    statement VARCHAR NOT NULL,
    figures BIGINT NOT NULL,
    mapped BIGINT NOT NULL,
    mapped_share DECIMAL(38, 6),
    claude_unverified BIGINT NOT NULL
);

-- Statement pages fin could not read in loaded and held documents (documents.unread_pages).
CREATE TABLE unread_pages (
    company VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    page INTEGER,
    statement VARCHAR,
    code VARCHAR,
    reason VARCHAR
);

-- Facts read by Claude (`fin ingest --reading`) or Docling (the PDF add-on) in loaded and held documents, not yet
-- verified (`fin verify`).
CREATE TABLE unverified_facts (
    company VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    period VARCHAR,
    statement VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    account VARCHAR,
    value DECIMAL(20, 2) NOT NULL,
    source VARCHAR                  -- page or sheet!cell
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
    unverified_claude_facts BIGINT,       -- current facts read by Claude or Docling, not yet verified
    held_documents BIGINT,                -- documents held with open findings (kept out of the metrics)
    open_findings BIGINT,                 -- findings still open
    accepted_findings BIGINT              -- findings accepted, kept as filed
);
