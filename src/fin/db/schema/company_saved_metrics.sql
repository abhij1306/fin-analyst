-- Part of the company schema. Runs after company_views.sql.

-- Saved metrics (`fin metric add`, fin.metrics.saved): metrics a user defines from accounts and other metrics.
-- shared: true for a metric saved for every company (its source is shared/dictionary.duckdb saved_metrics, copied
-- here); false for one saved for this company only. formula: as the user wrote it. periods: 'annual' when it uses
-- an annual-only metric, else 'all'. uses: the saved metrics its formula names. position: display order.
-- The values are computed by the view saved_metrics_wide (company_metrics.sql), which fin rewrites from these rows.
CREATE TABLE saved_metrics (
    metric VARCHAR PRIMARY KEY,
    unit VARCHAR NOT NULL CHECK (unit IN ('ratio', 'rupees', 'days')),
    periods VARCHAR NOT NULL CHECK (periods IN ('annual', 'all')),
    shared BOOLEAN NOT NULL,
    definition VARCHAR NOT NULL,
    formula VARCHAR NOT NULL,
    uses VARCHAR[] NOT NULL,
    position INTEGER NOT NULL,
    added_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL
);

-- What each saved metric is computed from, for metric_input_accounts: an account it names (kind 'account'), or a
-- built-in metric it names (kind 'metric', whose inputs it then has). A saved metric named in a formula is
-- replaced by what it is computed from.
CREATE TABLE saved_metric_operands (
    metric VARCHAR NOT NULL,
    operand VARCHAR NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('account', 'metric')),
    PRIMARY KEY (metric, operand)
);
