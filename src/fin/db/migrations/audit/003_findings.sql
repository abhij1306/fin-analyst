-- Findings: a difference worth showing the user, not a failure. Append-only like the other audit tables.
--
-- First used by `fin verify` (check_name 'verify_reading'): one row per line where an independent reading of a
-- document (figures Claude read from its pages or cells) and the loaded figures do not agree. finding is 'differ'
-- (both values), 'missing_in_fin' (a line, or a whole statement, the document did not load) or 'missing_in_reading'
-- (a loaded line of a statement, scope and period the reading covers that the reading does not have).
--
-- The columns are general so other checks can record findings here too: fin_* is the loaded figure, other_* what it
-- was compared with (a reading here; another document or a computed total for another check); difference is
-- other_value - fin_value when both exist.
CREATE TABLE findings (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    check_name VARCHAR NOT NULL,
    finding VARCHAR NOT NULL,
    doc_id INTEGER,
    file_name VARCHAR,
    statement VARCHAR,
    scope VARCHAR,
    period VARCHAR,
    label VARCHAR,
    fin_value DECIMAL(20, 2),
    other_value DECIMAL(20, 2),
    difference DECIMAL(20, 2),
    fin_printed VARCHAR,
    other_printed VARCHAR,
    fin_source VARCHAR,
    other_source VARCHAR,
    other_file VARCHAR,
    other_sha256 VARCHAR,
    fact_id BIGINT,
    read_by VARCHAR,
    message VARCHAR
);
