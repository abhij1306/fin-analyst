-- The rest of the PLAN.md §8 audit tables, created up front so later phases only append rows.
-- Append-only like 001: fin only ever INSERTs into these tables.

-- Phase 5: every mapping decision. actor is 'dictionary', 'claude' or 'human'; on_request is true when Claude
-- acted because the user asked (for example `fin review confirm` run by Claude).
CREATE TABLE mapping_decisions (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR,
    statement VARCHAR,
    section VARCHAR,
    raw_label VARCHAR NOT NULL,
    account VARCHAR,
    scope VARCHAR,
    decision VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    on_request BOOLEAN NOT NULL DEFAULT false,
    confidence DOUBLE,
    reason VARCHAR
);

-- Phase 6: one row per validation check run on a document.
CREATE TABLE validation_results (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL,
    file_name VARCHAR,
    check_name VARCHAR NOT NULL,
    fiscal_year VARCHAR,
    scope VARCHAR,
    expected DECIMAL(20, 2),
    actual DECIMAL(20, 2),
    tolerance DECIMAL(20, 2),
    passed BOOLEAN NOT NULL,
    error_code VARCHAR,
    message VARCHAR
);

-- Phase 8: questions answered with `fin query`, and data changes made with `fin sql --write`.
CREATE TABLE queries (
    query_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR,
    question VARCHAR,
    sql VARCHAR NOT NULL,
    mode VARCHAR NOT NULL CHECK (mode IN ('read', 'write')),
    row_count BIGINT,
    result_hash VARCHAR,
    evidence_path VARCHAR,
    backup_path VARCHAR
);

-- Phase 9: rendered reports.
CREATE TABLE reports (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    output_paths VARCHAR NOT NULL,
    template_commit VARCHAR,
    data_hash VARCHAR NOT NULL,
    narrative_path VARCHAR
);

-- Phase 11: company deletions.
CREATE TABLE deletions (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    files_removed VARCHAR NOT NULL,
    row_counts VARCHAR,
    backup_path VARCHAR NOT NULL
);
