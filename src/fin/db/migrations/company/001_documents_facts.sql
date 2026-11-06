-- Company database, companies/<company>.duckdb (PLAN.md §7). Money is DECIMAL(20,2) rupees.
-- No foreign keys: DuckDB cannot update a row that other rows reference (documents.status changes when a
-- document is superseded). The id columns are still the links: facts.raw_id -> raw_values.raw_id, and
-- raw_values.doc_id and facts.doc_id -> documents.doc_id.

CREATE SEQUENCE documents_seq START 1;
CREATE SEQUENCE raw_values_seq START 1;
CREATE SEQUENCE facts_seq START 1;

-- One row per ingested file. Ingesting the same file again with --force adds a new version and marks the
-- earlier one 'superseded' (superseded_by = the new doc_id). Its rows stay, so use status = 'loaded'.
CREATE TABLE documents (
    doc_id INTEGER PRIMARY KEY DEFAULT nextval('documents_seq'),
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    fiscal_year VARCHAR NOT NULL,   -- the latest fiscal year in the document, e.g. FY25
    scope VARCHAR NOT NULL,         -- standalone, consolidated, or standalone,consolidated
    status VARCHAR NOT NULL CHECK (status IN ('loaded', 'superseded')),
    archive_path VARCHAR NOT NULL,  -- archive/<company>/<sha256>.<ext>, relative to the data folder
    run_id VARCHAR NOT NULL,
    superseded_by INTEGER,
    ingested_at TIMESTAMP NOT NULL,
    UNIQUE (sha256, version)
);

-- One row per value cell, exactly as found. sheet + cell for Excel; page + bbox for PDF (Phase 4).
CREATE TABLE raw_values (
    raw_id BIGINT PRIMARY KEY DEFAULT nextval('raw_values_seq'),
    doc_id INTEGER NOT NULL,
    sheet VARCHAR,
    cell VARCHAR,
    page INTEGER,
    bbox VARCHAR,
    statement VARCHAR NOT NULL CHECK (statement IN ('balance_sheet', 'profit_and_loss', 'cash_flow')),
    section VARCHAR NOT NULL,       -- the heading the line sits under, as written ('' if none)
    raw_label VARCHAR NOT NULL,
    raw_value_text VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL,      -- the unit line as written, e.g. (₹ in Lakhs)
    rupees_per_unit BIGINT NOT NULL,
    run_id VARCHAR NOT NULL
);

-- One row per raw value, in rupees. account is NULL and confirmed is false while the label is unmapped
-- (E301). mapping_id is shared/dictionary.duckdb label_map.mapping_id.
CREATE TABLE facts (
    fact_id BIGINT PRIMARY KEY DEFAULT nextval('facts_seq'),
    raw_id BIGINT NOT NULL,
    doc_id INTEGER NOT NULL,
    account VARCHAR,
    fiscal_year VARCHAR NOT NULL,
    period_end DATE NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    value DECIMAL(20, 2) NOT NULL,
    mapping_id INTEGER,
    confirmed BOOLEAN NOT NULL,
    run_id VARCHAR NOT NULL
);

-- Notes to accounts and auditor's report sections, as Markdown files in notes/<company>/ (Phase 4).
CREATE TABLE notes (
    doc_id INTEGER NOT NULL,
    section VARCHAR NOT NULL,
    markdown_path VARCHAR NOT NULL
);
