-- Findings instead of failures. fin is an analysis tool: when what a company filed does not add up (balance sheet
-- not tying, a subtotal not equal to its lines, cash not reconciling, a figure differing between documents or from
-- a Claude reading), that is a finding to show, not a failure that rejects the data (fin.findings).
--
-- documents.status 'held': staged with open findings and kept out of current figures (document_facts still has
-- its facts; current_facts, metrics and the cross-document check use only 'loaded') until the user decides:
-- `fin findings accept` loads it as filed, or a fix and `fin findings recheck` (or an ingest --force) clears it.
-- DuckDB cannot change a CHECK constraint, so the table is rebuilt (views bind late and keep working).

CREATE TABLE documents_new (
    doc_id INTEGER PRIMARY KEY DEFAULT nextval('documents_seq'),
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    fiscal_year VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('loaded', 'held', 'superseded')),
    archive_path VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    superseded_by INTEGER,
    ingested_at TIMESTAMP NOT NULL,
    period_end DATE,
    reading_sha256 VARCHAR,
    reading_path VARCHAR,
    UNIQUE (sha256, version)
);
INSERT INTO documents_new BY NAME SELECT * FROM documents;
DROP TABLE documents;
ALTER TABLE documents_new RENAME TO documents;

-- One row per finding, with its current status:
--   open       found, waiting for the user's decision (a held document stays held while it has one);
--   accepted   the user chose to keep the figures as filed (`fin findings accept`); still shown;
--   resolved   a later run of the same check no longer finds it (after a fix);
--   superseded its document was replaced by a new version (ingest --force).
-- Columns as in the audit table `findings` (audit migration 003): fin_* is the figure in this document, other_* what
-- it was compared with (the other side of the balance sheet, the sum of a subtotal's lines, the balance-sheet cash,
-- another document's figure, or a Claude reading). code is the check's §9 code (E401-E405), NULL for verify_reading.
-- Every finding and every status change is also appended to the audit table.
CREATE SEQUENCE findings_seq START 1;
CREATE TABLE findings (
    finding_id BIGINT PRIMARY KEY DEFAULT nextval('findings_seq'),
    doc_id INTEGER,
    file_name VARCHAR,
    check_name VARCHAR NOT NULL,
    code VARCHAR,
    finding VARCHAR NOT NULL,
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
    message VARCHAR,
    status VARCHAR NOT NULL CHECK (status IN ('open', 'accepted', 'resolved', 'superseded')),
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL
);
