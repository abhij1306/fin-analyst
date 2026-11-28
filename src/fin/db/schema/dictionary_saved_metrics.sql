-- Part of the dictionary schema: metrics saved for every company (`fin metric add` without --company,
-- fin.metrics.saved). Each company database holds a copy (its saved_metrics rows with shared = true).
CREATE TABLE saved_metrics (
    metric VARCHAR PRIMARY KEY,
    unit VARCHAR NOT NULL CHECK (unit IN ('ratio', 'rupees', 'days')),
    definition VARCHAR NOT NULL,
    formula VARCHAR NOT NULL,
    added_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL
);
