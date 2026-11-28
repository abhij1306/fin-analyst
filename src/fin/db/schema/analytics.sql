-- shared/analytics.duckdb (docs/product.md §7): every company's metrics in one file for the dashboards.
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

-- Standard statements (fin.statements): every line of every period and scope, in the company's display unit
-- (config.toml [display], converted in SQL; unit is its caption, e.g. 'INR Mn'; per-share lines as printed, unit
-- '₹ per share'). line_type 'other' is the
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
    value DECIMAL(38, 6),
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
    mapped_share DECIMAL(38, 6)
);

-- Statement pages (and Excel sheets: sheet set, page NULL) fin could not read in loaded and held documents
-- (documents.unread_pages).
CREATE TABLE unread_pages (
    company VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    page INTEGER,
    sheet VARCHAR,
    statement VARCHAR,
    code VARCHAR,
    reason VARCHAR
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
    held_documents BIGINT,                -- documents held with open findings (kept out of the metrics)
    open_findings BIGINT,                 -- findings still open
    accepted_findings BIGINT              -- findings accepted, kept as filed
);

-- Diligence analytics on the registers (the company views customer_concentration, customer_retention, sector_mix and
-- product_pvm_summary, docs/diligence.md D9): money in rupees, shares as fractions. Rows only for a company with an
-- engagement and a loaded sales register.
CREATE TABLE customer_concentration (
    company VARCHAR NOT NULL,
    period VARCHAR,
    basis VARCHAR,
    segment VARCHAR,
    customers INTEGER,
    revenue DECIMAL(38, 2),
    top1_share DECIMAL(38, 6),
    top5_share DECIMAL(38, 6),
    top10_share DECIMAL(38, 6)
);

CREATE TABLE customer_retention (
    company VARCHAR NOT NULL,
    period VARCHAR,
    prior_period VARCHAR,
    prior_customers INTEGER,
    retained_customers INTEGER,
    lost_customers INTEGER,
    new_customers INTEGER,
    prior_revenue DECIMAL(38, 2),
    retained_revenue DECIMAL(38, 2),
    lost_revenue DECIMAL(38, 2),
    new_revenue DECIMAL(38, 2),
    net_retention DECIMAL(38, 6),
    gross_retention DECIMAL(38, 6),
    logo_retention DECIMAL(38, 6)
);

CREATE TABLE sector_mix (
    company VARCHAR NOT NULL,
    period VARCHAR,
    basis VARCHAR,
    segment VARCHAR,
    sector VARCHAR,
    customers INTEGER,
    revenue DECIMAL(38, 2),
    share DECIMAL(38, 6)
);

CREATE TABLE price_volume_mix (
    company VARCHAR NOT NULL,
    period VARCHAR,
    prior_period VARCHAR,
    price_effect DECIMAL(38, 2),
    volume_effect DECIMAL(38, 2),
    mix_effect DECIMAL(38, 2),
    new_revenue DECIMAL(38, 2),
    discontinued_revenue DECIMAL(38, 2),
    change DECIMAL(38, 2)
);

-- QoE outputs (the company views adjusted_ebitda, revenue_split, ebitda_to_cash and nwc_series, docs/diligence.md
-- D7): money in rupees, ratios as fractions. Rows only for a company with an engagement.
CREATE TABLE qoe_ebitda (
    company VARCHAR NOT NULL,
    period VARCHAR,
    scope VARCHAR,
    revenue DECIMAL(38, 2),
    adjusted_revenue DECIMAL(38, 2),
    reported_ebitda DECIMAL(38, 2),
    accepted_adjustments DECIMAL(38, 2),
    adjusted_ebitda DECIMAL(38, 2),
    reported_ebitda_margin DECIMAL(38, 6),
    adjusted_ebitda_margin DECIMAL(38, 6),
    proposed_adjustments DECIMAL(38, 2)
);

CREATE TABLE qoe_revenue (
    company VARCHAR NOT NULL,
    period VARCHAR,
    basis VARCHAR,
    core_revenue DECIMAL(38, 2),
    passthrough_revenue DECIMAL(38, 2),
    passthrough_share DECIMAL(38, 6),
    core_revenue_growth DECIMAL(38, 6)
);

CREATE TABLE qoe_walk (
    company VARCHAR NOT NULL,
    period VARCHAR,
    scope VARCHAR,
    ebitda DECIMAL(38, 2),
    adjustments DECIMAL(38, 2),
    adjusted_ebitda DECIMAL(38, 2),
    non_cash_and_other DECIMAL(38, 2),
    working_capital_change DECIMAL(38, 2),
    taxes_paid DECIMAL(38, 2),
    other_operating DECIMAL(38, 2),
    cfo DECIMAL(38, 2),
    capex DECIMAL(38, 2),
    free_cash_flow DECIMAL(38, 2),
    cash_conversion DECIMAL(38, 6)
);

CREATE TABLE qoe_nwc (
    company VARCHAR NOT NULL,
    period VARCHAR,
    granularity VARCHAR,
    as_of DATE,
    basis VARCHAR,
    trade_receivables DECIMAL(38, 2),
    inventories DECIMAL(38, 2),
    other_current_assets DECIMAL(38, 2),
    trade_payables DECIMAL(38, 2),
    other_current_liabilities DECIMAL(38, 2),
    net_working_capital DECIMAL(38, 2),
    unmapped_ledgers BIGINT,
    ledgers_included BIGINT,
    ledgers_excluded BIGINT,
    missing_components VARCHAR  -- the components with no ledger, left out of the net (nwc_year_end)
);
