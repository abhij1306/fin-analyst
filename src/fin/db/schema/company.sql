-- Company database, companies/<company>.duckdb (PLAN.md §7): the tables. The metric macros and views follow in
-- company_views.sql, which is run right after this file. Money is DECIMAL(20,2) rupees.
-- No foreign keys: DuckDB cannot update a row that other rows reference (documents.status changes when a
-- document is superseded). The id columns are still the links: facts.raw_id -> raw_values.raw_id, and
-- raw_values.doc_id and facts.doc_id -> documents.doc_id.

CREATE SEQUENCE documents_seq START 1;
CREATE SEQUENCE raw_values_seq START 1;
CREATE SEQUENCE facts_seq START 1;
CREATE SEQUENCE findings_seq START 1;

-- One row per ingested file. Ingesting the same file again with --force adds a new version and marks the
-- earlier one 'superseded' (superseded_by = the new doc_id). Its rows stay, so use status = 'loaded'.
-- status 'held': staged with open findings and kept out of current figures (document_facts still has its facts;
-- current_facts, metrics and the cross-document check use only 'loaded') until the user decides: `fin findings
-- accept` loads it as filed, or a fix and `fin findings recheck` (or an ingest --force) clears it.
-- period_end is the latest period end in the document; its facts that end then are its own periods.
-- reading_sha256 / reading_path: the Claude reading file a document was loaded with (`fin ingest --reading`,
-- archived under archive/<company>/), NULL when fin read the whole document.
CREATE TABLE documents (
    doc_id INTEGER PRIMARY KEY DEFAULT nextval('documents_seq'),
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    fiscal_year VARCHAR NOT NULL,   -- the latest fiscal year in the document, e.g. FY25
    scope VARCHAR NOT NULL,         -- standalone, consolidated, or standalone,consolidated
    status VARCHAR NOT NULL CHECK (status IN ('loaded', 'held', 'superseded')),
    archive_path VARCHAR NOT NULL,  -- archive/<company>/<sha256>.<ext>, relative to the data folder
    run_id VARCHAR NOT NULL,
    superseded_by INTEGER,
    ingested_at TIMESTAMP NOT NULL,
    period_end DATE,
    reading_sha256 VARCHAR,
    reading_path VARCHAR,
    UNIQUE (sha256, version)
);

-- One row per value cell, exactly as found. sheet + cell for Excel; page + bbox for PDF.
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
--
-- period: 'FY25' for the year ended 31 March 2025, 'FY26-Q1' ... 'FY26-Q4' for quarters, 'FY26-H1' / 'FY26-H2' for
-- half years, 'FY26-9M' for nine months (fin.extract.periods). period_months is its length (3, 6, 9 or 12). A
-- balance sheet is a point in time: 30 Sep 2025 is 'FY26-H1' (6), 31 Mar 2025 is 'FY25' (12). fiscal_year is the
-- fiscal year the period falls in ('FY26' for 'FY26-Q1'). Ingest always sets period and period_months.
--
-- read_by: 'fin' (fin's own extraction) or 'claude' (from a reading file). Either way the fact points at the
-- original document's page or sheet!cell through raw_values.
-- verified: true once `fin verify` found a reading that agrees with the fact in a loaded document. A fact read by
-- Claude is verified only by a reading other than the one it was loaded from. A later verify that differs sets it
-- back to false.
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
    run_id VARCHAR NOT NULL,
    period VARCHAR,
    period_months INTEGER,
    read_by VARCHAR DEFAULT 'fin',
    verified BOOLEAN DEFAULT false
);

-- Notes to accounts and auditor's report sections, as Markdown files in notes/<company>/.
CREATE TABLE notes (
    doc_id INTEGER NOT NULL,
    section VARCHAR NOT NULL,
    markdown_path VARCHAR NOT NULL
);

-- Findings instead of failures. fin is an analysis tool: when what a company filed does not add up (balance sheet
-- not tying, a subtotal not equal to its lines, cash not reconciling, a figure differing between documents or from
-- a Claude reading), that is a finding to show, not a failure that rejects the data (fin.findings).
--
-- One row per finding, with its current status:
--   open       found, waiting for the user's decision (a held document stays held while it has one);
--   accepted   the user chose to keep the figures as filed (`fin findings accept`); still shown;
--   resolved   a later run of the same check no longer finds it (after a fix);
--   superseded its document was replaced by a new version (ingest --force).
-- Columns as in the audit table `findings`: fin_* is the figure in this document, other_* what it was compared with
-- (the other side of the balance sheet, the sum of a subtotal's lines, the balance-sheet cash, another document's
-- figure, or a Claude reading). code is the check's §9 code (E401-E405), NULL for verify_reading.
-- Every finding and every status change is also appended to the audit table.
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
