-- Company database, companies/<company>.duckdb (docs/product.md §7): the tables. The metric macros and views follow in
-- company_views.sql, which is run right after this file. Money is DECIMAL(20,2) rupees.
-- No foreign keys: DuckDB cannot update a row that other rows reference (documents.status changes when a
-- document is superseded). The id columns are still the links: facts.raw_id -> raw_values.raw_id, and
-- raw_values.doc_id and facts.doc_id -> documents.doc_id.

CREATE SEQUENCE documents_seq START 1;
CREATE SEQUENCE raw_values_seq START 1;
CREATE SEQUENCE facts_seq START 1;
CREATE SEQUENCE findings_seq START 1;
CREATE SEQUENCE stated_figures_seq START 1;
CREATE SEQUENCE fact_corrections_seq START 1;

-- One row per ingested file. Ingesting the same file again with --force adds a new version and marks the
-- earlier one 'superseded' (superseded_by = the new doc_id). Its rows stay, so use status = 'loaded'.
-- status 'held': staged with open findings and kept out of current figures (document_facts still has its facts;
-- current_facts, metrics and the cross-document check use only 'loaded') until the user decides: `fin findings
-- accept` loads it as filed, or a fix and `fin findings recheck` (or an ingest --force) clears it.
-- period_end is the latest period end in the document; its facts that end then are its own periods.
-- reading_sha256 / reading_path: the Claude reading file a document was loaded with (`fin ingest --reading`,
-- archived under archive/<company>/), NULL when fin read the whole document.
-- unread_pages: the statement pages fin could not read, as JSON ([{"page", "statement", "code", "reason"}]);
-- NULL when every statement page was read. An Excel sheet held for values with no label (E204) is an entry with
-- "sheet" (and "page" null).
-- unit_given / unit_given_by: the unit the user gave for statements with no unit line (`fin ingest --unit`), and
-- the actor who entered it ('user' or 'ai:claude'); its rows' raw_unit says "unit given by user". NULL otherwise.
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
    unread_pages VARCHAR,
    unit_given VARCHAR,
    unit_given_by VARCHAR,
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
-- read_by: 'fin' (fin's own extraction, pdfplumber or openpyxl), 'docling', 'liteparse' or 'llamaparse' (the
-- optional PDF add-on's engine, on a page pdfplumber could not read) or 'ai:claude' (from a reading file). Either way the fact points at the original
-- document's page or sheet!cell through raw_values. read_by is information about the source; whether a figure read
-- by Claude or the PDF add-on is trusted is the user's decision. The checks `fin verify` ran on a document are in
-- verify_runs and verify_lines.
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
    read_by VARCHAR DEFAULT 'fin'
);

-- The accounts the user added in review (shared/dictionary.duckdb accounts, source 'review') that add into a subtotal,
-- copied here whenever facts are written (fin.map.dictionary.copy_review_accounts) so a stand-in total (stand_ins)
-- sums every dictionary child of the total, with its sign.
CREATE TABLE review_accounts (
    account VARCHAR PRIMARY KEY,
    parent VARCHAR NOT NULL,
    sign INTEGER NOT NULL CHECK (sign IN (1, -1))
);

-- Figures the user states (`fin fact state`, fin.facts): a nil for a line the documents do not have, or a value from a
-- source fin cannot hold. Not a document's figure: read_by is always 'user', and reason is the user's words (as typed;
-- not masked). current_facts uses a stated figure only while no loaded document gives the same account, period and
-- scope; stating one a loaded document gives stores nothing here and records a finding instead. status 'withdrawn'
-- (`fin fact unstate`) keeps the row with the run and time it was withdrawn. actor (fin.actor) is who ran
-- `fin fact state`: 'user', or 'ai:<provider>' acting on the user's word; the figure is the user's either way.
-- period, period_months, period_end and fiscal_year are as in facts. value is in rupees (rupees per share for EPS).
CREATE TABLE stated_figures (
    stated_id BIGINT PRIMARY KEY DEFAULT nextval('stated_figures_seq'),
    account VARCHAR NOT NULL,
    fiscal_year VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    period_months INTEGER NOT NULL,
    period_end DATE NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    value DECIMAL(20, 2) NOT NULL,
    reason VARCHAR NOT NULL,
    read_by VARCHAR NOT NULL DEFAULT 'user' CHECK (read_by = 'user'),
    actor VARCHAR NOT NULL CHECK (actor = 'user' OR actor LIKE 'ai:%'),
    status VARCHAR NOT NULL CHECK (status IN ('stated', 'withdrawn')),
    run_id VARCHAR NOT NULL,
    stated_at TIMESTAMP NOT NULL,
    withdrawn_run_id VARCHAR,
    withdrawn_at TIMESTAMP
);

-- Corrections of a figure fin read wrongly (`fin fact correct`, fin.facts): one row per correction, the latest per
-- fact_id is the one in force. The correction sets facts.value; raw_values keeps the cell or page and the text as
-- printed, and original_value is facts.value before this correction. A correction is for fin's own misread, never to
-- make figures tie: inside a diligence engagement a figure under a query or an adjustment is refused (E409). actor
-- (fin.actor) is 'user', or 'ai:<provider>' with on_request true when an AI ran it on the user's word.
CREATE TABLE fact_corrections (
    correction_id BIGINT PRIMARY KEY DEFAULT nextval('fact_corrections_seq'),
    fact_id BIGINT NOT NULL,
    doc_id INTEGER NOT NULL,
    original_value DECIMAL(20, 2) NOT NULL,
    value DECIMAL(20, 2) NOT NULL,
    reason VARCHAR NOT NULL,
    actor VARCHAR NOT NULL CHECK (actor = 'user' OR actor LIKE 'ai:%'),
    on_request BOOLEAN NOT NULL,
    run_id VARCHAR NOT NULL,
    corrected_at TIMESTAMP NOT NULL
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
    file_id INTEGER,                -- a diligence source file's finding (source_files.file_id); doc_id is then NULL
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
    mappings VARCHAR,               -- JSON: the reviewed label mappings behind the figures (fin.validate.finding_rows)
    status VARCHAR NOT NULL CHECK (status IN ('open', 'accepted', 'resolved', 'superseded')),
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL
);

-- The checks that did not run on a document (fin.validate.not_run): replaced each time the document is checked.
-- period and scope are NULL for a check over the whole document (cross_document); needs is what would make it run.
CREATE TABLE checks_not_run (
    doc_id INTEGER NOT NULL,
    check_name VARCHAR NOT NULL,
    period VARCHAR,
    scope VARCHAR,
    reason VARCHAR NOT NULL,
    needs VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL
);

-- `fin verify` (fin.verify): one row per run that compared a Claude reading with a loaded or held document, any
-- number of times per document. The counts are the outcomes of its lines: agree, differ, missing_in_fin (the reading
-- has a line the document did not load), missing_in_reading (a loaded line of a statement, scope and period the
-- reading covers that the reading does not have). Every outcome other than agree is also a finding
-- (check_name 'verify_reading').
CREATE TABLE verify_runs (
    run_id VARCHAR PRIMARY KEY,
    doc_id INTEGER NOT NULL,
    file_name VARCHAR NOT NULL,
    reading_file VARCHAR NOT NULL,
    reading_sha256 VARCHAR NOT NULL,
    verified_at TIMESTAMP NOT NULL,
    agree INTEGER NOT NULL,
    differ INTEGER NOT NULL,
    missing_in_fin INTEGER NOT NULL,
    missing_in_reading INTEGER NOT NULL
);

-- Every line a verify run compared, in order (line_no). fin_* is what is loaded (fin_value: facts.value in rupees,
-- or rupees per share for EPS); reading_* the reading's figure in the same unit, as printed, and where. A side
-- that has no line is NULL. fact_id and read_by are the loaded fact's.
CREATE TABLE verify_lines (
    run_id VARCHAR NOT NULL,
    line_no INTEGER NOT NULL,
    outcome VARCHAR NOT NULL CHECK (outcome IN ('agree', 'differ', 'missing_in_fin', 'missing_in_reading')),
    statement VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    fact_id BIGINT,
    read_by VARCHAR,
    fin_value DECIMAL(20, 2),
    reading_value DECIMAL(20, 2),
    fin_printed VARCHAR,
    reading_printed VARCHAR,
    fin_source VARCHAR,
    reading_source VARCHAR,
    PRIMARY KEY (run_id, line_no)
);

-- The company's name as the user typed it when creating the company on the Files screen (fin.services.files); its
-- code is this database's file name. No row for a company that was made by ingesting into inbox/<company>/.
CREATE TABLE company_profile (
    code VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL
);
