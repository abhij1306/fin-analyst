-- Periods (quarterly and half-year results): analytics.metrics gets the period of each row. The table is rebuilt
-- in full by every `fin metrics refresh`, so it is recreated empty here.
-- period: 'FY25' for a year, 'FY26-Q1' for a quarter, 'FY26-H1' for a half year, 'FY26-9M' for nine months;
-- period_months: its length (12, 3, 6, 9). Annual rows have period = fiscal_year.

DROP TABLE metrics;

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
