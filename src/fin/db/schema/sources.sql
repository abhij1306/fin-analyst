-- Company database, companies/<company>.duckdb: diligence source files and their rows (docs/DILIGENCE_PLAN.md D2,
-- fin.diligence.sources). Run after diligence.sql. Money is DECIMAL(20,2) rupees. No foreign keys: file_id is the
-- link. Every row keeps its lineage: the file (sha256, archive path) and sheet + row.
-- Masked names: party ledgers (under the groups a column profile names, e.g. Sundry Debtors and Sundry Creditors)
-- are stored as tokens (CUST-0001, VEND-0001, LEDG-0001); mask_values holds the real values and is never printed
-- (`fin sql` refuses to read it, E810).

CREATE SEQUENCE source_files_seq START 1;

-- One row per source file loaded with `fin ingest <co> <file> --kind <kind>`.
-- kind: 'trial_balance' or 'ledger_vouchers' (D3 adds registers, bank, payroll). period: the annual period of the
-- engagement the export covers, read from its header lines (period_start, period_end). basis: 'audited' for an
-- annual period of the engagement ('unaudited' comes with the year-to-date stub). profile: the column profile used
-- (templates/profiles/<profile>.toml).
--   loaded      its rows are current;
--   superseded  a later file of the same kind and period replaced it (superseded_by); its rows stay.
CREATE TABLE source_files (
    file_id INTEGER PRIMARY KEY DEFAULT nextval('source_files_seq'),
    engagement_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('trial_balance', 'ledger_vouchers')),
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    archive_path VARCHAR NOT NULL,  -- archive/<company>/<sha256>.<ext>, relative to the data folder
    period VARCHAR NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    basis VARCHAR NOT NULL CHECK (basis IN ('audited', 'unaudited')),
    profile VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('loaded', 'superseded')),
    superseded_by INTEGER,
    run_id VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL
);

-- The real value behind each token, stored once per kind: the same name gets the same token in every file.
-- kind: customer, vendor, user, employee_name, employee_code, or ledger (a Ledger Vouchers ledger not in its
-- period's trial balance: fin cannot tell whether it is a party, so it is masked). match_key: the value in lower
-- case with spaces collapsed. Read only by the accountant export (D8); never printed.
CREATE TABLE mask_values (
    token VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    real_value VARCHAR NOT NULL,
    match_key VARCHAR NOT NULL,
    first_file_id INTEGER NOT NULL,
    run_id VARCHAR NOT NULL,
    UNIQUE (kind, match_key)
);

-- Trial balance rows as filed (Tally Detailed export: closing balances only). row_type 'group' (a group total;
-- group_name is the group, parent_group the group it sits in) or 'ledger' (ledger is the ledger name, or its token
-- for a party ledger; group_name the group it sits in). A group not equal to the sum of its ledgers is a finding
-- (check_name 'tb_group_sum'), and the rows stay as filed.
CREATE TABLE trial_balance (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    period VARCHAR NOT NULL,
    row_type VARCHAR NOT NULL CHECK (row_type IN ('group', 'ledger')),
    group_name VARCHAR,
    parent_group VARCHAR,
    ledger VARCHAR,
    closing_debit DECIMAL(20, 2) NOT NULL,
    closing_credit DECIMAL(20, 2) NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- Ledger Vouchers rows: one per voucher line, the ledger taken from the block header above it (token for a party
-- ledger). particulars: the other ledger, with known masked names replaced by their tokens. Opening, closing and
-- total rows are not loaded.
CREATE TABLE ledger_entries (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    ledger VARCHAR NOT NULL,
    entry_date DATE NOT NULL,
    particulars VARCHAR,
    voucher_type VARCHAR,
    voucher_no VARCHAR,
    debit DECIMAL(20, 2) NOT NULL,
    credit DECIMAL(20, 2) NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);
