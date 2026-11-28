-- Company database, companies/<company>.duckdb: diligence source files and their rows (docs/diligence.md D2,
-- fin.diligence.sources). Run after diligence.sql. Money is DECIMAL(20,2) rupees. No foreign keys: file_id is the
-- link. Every row keeps its lineage: the file (sha256, archive path) and sheet + row.
-- Masked names (the client's instruction, docs/decisions/masking.md): the registers' customer, vendor, handler and
-- submitted-by, the orderbook's customer and payroll's employee name and code are stored as tokens (CUST-0001,
-- VEND-0001, USER-0001, EMP-0001, ECODE-0001), and a bank statement's account number too (ACCT-0001, the client,
-- 2026-11-27: bank_statements, bank_lines); the books and bank text keep names as filed. mask_values holds
-- the real values and is never printed (`fin sql` refuses to read it, E810).

CREATE SEQUENCE source_files_seq START 1;

-- One row per source file loaded with `fin ingest <co> <file> --kind <kind>`.
-- kind: 'trial_balance', 'ledger_vouchers', 'registers', 'payroll', 'bank', 'orderbook' (F7, fdd_orderbook.sql), or a
-- GST return summary 'gstr1' or 'gstr3b' (F6, fdd_gst.sql). period: the period of the engagement
-- the export covers, read from its header lines (period_start, period_end): an annual period, or the stub (a bank
-- statement covers part of one: the period that holds all its dates). A registers
-- file has no period header: period_start and period_end are its first and last invoice date, period names the fiscal
-- years they fall in ('FY25', or 'FY24-FY25'), and basis is 'unaudited' when any invoice date is in the stub. basis:
-- 'audited' for an annual period, 'unaudited' for the stub (unaudited management accounts). profile: the column profile used
-- (templates/profiles/<profile>.toml).
--   loaded      its rows are current;
--   superseded  a later file of the same kind and period replaced it (superseded_by); its rows stay.
CREATE TABLE source_files (
    file_id INTEGER PRIMARY KEY DEFAULT nextval('source_files_seq'),
    engagement_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('trial_balance', 'ledger_vouchers', 'registers', 'payroll', 'bank', 'orderbook',
        'gstr1', 'gstr3b')),
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
-- kind: customer, vendor, user, employee_name, employee_code or bank_account. match_key: the value in lower case with
-- spaces collapsed; a ledger or a bank line naming a customer or vendor is matched to its token by it (party matching).
-- real_value is read only by the accountant export (D8); never printed. first_file_id: the file whose load made it;
-- NULL for a bank account number masked when its PDF's parse was saved (fin.diligence.bank_mask), before any load.
CREATE TABLE mask_values (
    token VARCHAR PRIMARY KEY,
    kind VARCHAR NOT NULL,
    real_value VARCHAR NOT NULL,
    match_key VARCHAR NOT NULL,
    first_file_id INTEGER,
    run_id VARCHAR NOT NULL,
    UNIQUE (kind, match_key)
);

-- Trial balance rows as filed (Tally Detailed export: closing balances only). row_type 'group' (a group total;
-- group_name is the group, parent_group the group it sits in) or 'ledger' (ledger is the ledger name as filed;
-- group_name the group it sits in). A group not equal to the sum of its ledgers is a finding
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

-- Ledger Vouchers rows: one per voucher line, the ledger taken from the block header above it, as
-- filed. particulars: the other ledger, as filed. Opening, closing and total rows are not loaded.
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

-- Ledger Vouchers ledger blocks: one per ledger block header the export has, with or without voucher lines (a ledger
-- with only an opening balance has none). Whether a trial balance ledger is "in the Ledger Vouchers" is read from here.
CREATE TABLE ledger_blocks (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    ledger VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- Payroll snapshot (fin.diligence.payroll): one row per employee, as the company's payroll system (Sparsh) lists them
-- on snapshot_date; no month column. employee_name and employee_code are tokens (EMP-0001, ECODE-0001). gross_salary
-- and ctc are ANNUAL rupees as filed, once the user confirms the unit (payroll_units); a blank cell is NULL. The
-- period of the file is the engagement period (or the stub) that contains snapshot_date.
CREATE TABLE payroll_lines (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    employee_name VARCHAR NOT NULL,
    employee_code VARCHAR NOT NULL,
    designation VARCHAR,
    department VARCHAR,
    vertical VARCHAR,
    sub_vertical VARCHAR,
    joining_date DATE,
    resign_date DATE,
    gross_salary DECIMAL(20, 2),
    ctc DECIMAL(20, 2),
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- Sales and purchase register lines (a billing-system workbook, fin.diligence.registers): one row per invoice line,
-- with sheet + row lineage. kind: 'sales' or 'purchase'. Amount in Rs (amount_inr) is before GST: the register has no
-- GST or tax columns. For a foreign-currency line, amount is in currency and amount_inr is what the register gives (not
-- recomputed). customer (sales), vendor (purchase), handler and submitted_by are tokens (CUST-, VEND-, USER-); the ids,
-- invoice_no and purchase_id (a sale's link to the OEM purchase) are as filed. The same invoice number twice is a
-- finding (check_name 'register_duplicate_invoice'); both rows stay. period: the engagement period the invoice date is
-- in (an annual period or the stub), NULL for a date outside the engagement.
CREATE TABLE register_lines (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('sales', 'purchase')),
    bill_id VARCHAR,
    purchase_id VARCHAR,
    customer_id VARCHAR,
    customer VARCHAR,
    vendor_id VARCHAR,
    vendor VARCHAR,
    location VARCHAR,
    invoice_no VARCHAR NOT NULL,
    contract_start DATE,
    contract_end DATE,
    invoice_date DATE NOT NULL,
    order_type VARCHAR,
    product VARCHAR,
    service_start DATE,
    service_end DATE,
    quantity DECIMAL(24, 6),
    rate DECIMAL(24, 6),
    amount DECIMAL(24, 4),
    currency VARCHAR,
    currency_rate DECIMAL(24, 6),
    amount_inr DECIMAL(20, 2) NOT NULL,
    service_type VARCHAR,
    handler VARCHAR,
    submitted_by VARCHAR,
    period VARCHAR,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- One row per bank statement file (kind 'bank', Excel or PDF): the account it is for and what it says it covers.
-- opening_balance / closing_balance come from the statement's header lines ('header') or, when it has none, from its
-- first row's balance minus its movement and its last row's balance ('rows'). ties: opening + credits - debits =
-- closing; a statement that does not tie has an open finding (check_name 'bank_tie') and stays as filed. Money is in
-- the account's own currency (INR or USD). reader: 'excel', or the PDF add-on that read it (docling, liteparse,
-- llamaparse); parse_path: the saved parse of a PDF (cache/bank/<company>/<sha256>.json, relative to the data folder).
CREATE TABLE bank_statements (
    file_id INTEGER PRIMARY KEY,
    bank VARCHAR NOT NULL,
    account VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    opening_balance DECIMAL(20, 2) NOT NULL,
    opening_from VARCHAR NOT NULL CHECK (opening_from IN ('header', 'rows')),
    closing_balance DECIMAL(20, 2) NOT NULL,
    closing_from VARCHAR NOT NULL CHECK (closing_from IN ('header', 'rows')),
    total_debits DECIMAL(20, 2) NOT NULL,
    total_credits DECIMAL(20, 2) NOT NULL,
    line_count INTEGER NOT NULL,
    ties BOOLEAN NOT NULL,
    reader VARCHAR NOT NULL,
    parse_path VARCHAR,
    run_id VARCHAR NOT NULL
);

-- Bank statement rows, one per transaction, with lineage: sheet and row (Excel), or page (PDF: sheet is 'page N',
-- row counts the table rows on that page, so it is the position in the saved parse). remarks and reference: text as
-- filed. Debit and credit are the movement out of and into the
-- account (blank is 0); balance is the running balance the bank printed (NULL when it printed none).
CREATE TABLE bank_lines (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    page INTEGER,
    "row" INTEGER NOT NULL,
    bank VARCHAR NOT NULL,
    account VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    txn_date DATE NOT NULL,
    remarks VARCHAR,
    reference VARCHAR,
    debit DECIMAL(20, 2) NOT NULL,
    credit DECIMAL(20, 2) NOT NULL,
    balance DECIMAL(20, 2),
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- The user's statement of a payroll file's unit (`fin payroll confirm-unit`): 'annual' (what the plan expects) or
-- 'monthly' (the file is not usable as it is: gate G1 fails until an annual snapshot is loaded). One row per file;
-- a later confirmation replaces it (every one is in the audit log).
CREATE TABLE payroll_units (
    file_id INTEGER PRIMARY KEY,
    unit VARCHAR NOT NULL CHECK (unit IN ('annual', 'monthly')),
    run_id VARCHAR NOT NULL,
    confirmed_at TIMESTAMP NOT NULL
);

-- Tally ledgers mapped to Schedule III dictionary accounts (fin.diligence.ledgers): one row per ledger as filed, for the
-- company. A trial balance load adds its new ledgers as 'pending'.
--   pending    not proposed yet;
--   proposed   Claude proposed an account (`fin map ledgers <co> <file>`): account, confidence, reason;
--   confirmed  the user confirmed it (`fin review confirm <co> <ids> --ledgers`); only confirmed ledgers count;
--   rejected   the user rejected the proposal.
-- statement and side come from the account when it is confirmed: side 1 for a debit-natured account (assets,
-- expenses), -1 for a credit-natured one (equity, liabilities, income), so (closing_debit - closing_credit) * side is
-- the balance as the statements show it. Whether a ledger is in a trial balance or Ledger Vouchers is read from those
-- tables (`fin map ledgers <co>` shows it).
CREATE SEQUENCE ledger_accounts_seq START 1;
CREATE TABLE ledger_accounts (
    ledger_id INTEGER PRIMARY KEY DEFAULT nextval('ledger_accounts_seq'),
    ledger VARCHAR NOT NULL UNIQUE,
    group_name VARCHAR,
    parent_group VARCHAR,
    status VARCHAR NOT NULL CHECK (status IN ('pending', 'proposed', 'confirmed', 'rejected')),
    account VARCHAR,
    statement VARCHAR CHECK (statement IN ('balance_sheet', 'profit_and_loss')),
    side INTEGER CHECK (side IN (1, -1)),
    confidence DOUBLE,
    reason VARCHAR,
    run_id VARCHAR NOT NULL,  -- the run of the last change
    changed_at TIMESTAMP NOT NULL
);

-- The current-year stub's figures: its loaded trial balance rolled up through the confirmed ledger mappings to
-- Schedule III accounts, one row per account. Unaudited management accounts (basis), never annualised; base figures
-- and growth use the audited years only. account NULL: the ledgers not confirmed to an account (value: their debit
-- - credit).
CREATE VIEW stub_figures AS
SELECT
    e.engagement_id,
    e.stub AS period,
    s.period_start,
    s.period_end,
    'unaudited management accounts' AS basis,
    m.account,
    m.statement,
    sum((t.closing_debit - t.closing_credit) * coalesce(m.side, 1)) AS value,
    count(*) AS ledgers,
    s.file_id
FROM engagement e
JOIN source_files s
    ON s.engagement_id = e.engagement_id AND s.period = e.stub AND s.kind = 'trial_balance'
    AND s.status = 'loaded'
JOIN trial_balance t ON t.file_id = s.file_id AND t.row_type = 'ledger'
LEFT JOIN ledger_accounts m ON m.ledger = t.ledger AND m.status = 'confirmed'
WHERE e.stub IS NOT NULL
GROUP BY e.engagement_id, e.stub, s.period_start, s.period_end, m.account, m.statement, s.file_id;
