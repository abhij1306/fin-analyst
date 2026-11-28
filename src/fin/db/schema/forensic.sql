-- The forensic layer (SPEC-18; docs/decisions/forensic.md): tests that look for entries made to mislead, on the
-- documents fin already loads. A flag is the company's to explain: it becomes a query (`fin query raise --flags`) or an
-- FDD finding the user approves; nothing is changed to clear it. No test scores or rates an entry.

-- One row per test and period each time `fin forensic run` runs it. Runs are kept: the latest per (engagement, test,
-- period) is current, and one whose input_hash no longer matches the data (its rows, mappings, threshold and forensic
-- settings) is shown as stale. outcome: 'ran' (flagged rows may be none), 'too_few' (population below minimum: nothing
-- flagged) or 'cannot_run' (needs says what is missing: a loaded file, or an input the user keeps on Settings).
-- settings: JSON of the settings the test used and the query threshold. near_misses: rows that fell just outside a
-- threshold (within 20% of it), for tuning (owner, 2026-11-28: "tune later but keep logs"). not_whole: rebuilt
-- vouchers whose debits do not equal their credits, left out of the voucher tests.
CREATE SEQUENCE forensic_runs_seq START 1;
CREATE TABLE forensic_runs (
    forensic_run_id INTEGER PRIMARY KEY DEFAULT nextval('forensic_runs_seq'),
    engagement_id INTEGER NOT NULL,
    test VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL CHECK (outcome IN ('ran', 'too_few', 'cannot_run')),
    population INTEGER,
    minimum INTEGER,
    needs VARCHAR,
    not_whole INTEGER,
    settings VARCHAR NOT NULL,
    flagged INTEGER NOT NULL DEFAULT 0,
    over_threshold INTEGER NOT NULL DEFAULT 0,
    near_misses INTEGER NOT NULL DEFAULT 0,
    input_hash VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- What a run flagged. Lineage: file_id + sheet + row (or page); source is the file and sheet!row in words (a voucher
-- gap names the file and the series). ledger as filed; account from a confirmed mapping (mapping_confirmed FALSE: the
-- ledger is named as itself and the reason says so); party: a customer's or vendor's token, never the name. amount:
-- NULL for a voucher gap, never 0. over_threshold: the amount is at or above the engagement's query threshold (a gap,
-- with no amount, counts as at or above): only those can be raised as a query.
CREATE SEQUENCE forensic_flags_seq START 1;
CREATE TABLE forensic_flags (
    flag_id INTEGER PRIMARY KEY DEFAULT nextval('forensic_flags_seq'),
    forensic_run_id INTEGER NOT NULL,
    test VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    file_id INTEGER,
    sheet VARCHAR,
    "row" INTEGER,
    page INTEGER,
    source VARCHAR NOT NULL,
    voucher_type VARCHAR,
    voucher_no VARCHAR,
    entry_date DATE,
    ledger VARCHAR,
    account VARCHAR,
    mapping_confirmed BOOLEAN,
    party VARCHAR,
    amount DECIMAL(20, 2) CHECK (amount IS NULL OR amount <> 0),
    reason VARCHAR NOT NULL,
    over_threshold BOOLEAN NOT NULL
);

-- The flags a query was raised on (`fin query raise --flags`), as query_items links recon items.
CREATE TABLE forensic_flag_queries (
    query_id INTEGER NOT NULL,
    flag_id INTEGER NOT NULL,
    PRIMARY KEY (query_id, flag_id)
);
