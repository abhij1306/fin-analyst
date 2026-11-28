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
-- llamaparse); parse_path: the saved parse of a PDF (cache/bank/<sha256>.json, relative to the data folder).
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

-- Reconciliations (fin.diligence.recon, fin.reconcile): one row per run of a named comparison for one period.
-- D4 adds sales_register_vs_gl and purchase_register_vs_gl (register lines vs the Ledger Vouchers entries of the
-- ledgers confirmed to revenue / purchases: left_file_id the first register file, right_file_id the Ledger Vouchers),
-- gl_vs_tb (Ledger Vouchers movements vs the trial balance: left_file_id the Ledger Vouchers, right_file_id the trial
-- balance) and payroll_vs_gl (left_file_id the payroll file, right_file_id the Ledger Vouchers). rpt_vs_books (FDD F5,
-- fin.disclosures.rpt; left_file_id the first disclosure_files reading, right_file_id the Ledger Vouchers or NULL;
-- level 'related_party' lines, key the party, books and ledger). tb_vs_statements
-- compares a period's trial balance (left_file_id), rolled up by confirmed ledger mapping, with the audited
-- standalone statement figures (current_facts). input_hash: sha256 of what it compared (the trial balance, the
-- ledger mappings, the figures, the threshold), so gate G1 can tell when it must run again. unmapped: trial balance
-- ledgers not confirmed to an account; while there are any, no query is drafted.
CREATE SEQUENCE recon_runs_seq START 1;
CREATE TABLE recon_runs (
    recon_id INTEGER PRIMARY KEY DEFAULT nextval('recon_runs_seq'),
    engagement_id INTEGER NOT NULL,
    name VARCHAR NOT NULL CHECK (name IN ('tb_vs_statements', 'sales_register_vs_gl',
        'purchase_register_vs_gl', 'gl_vs_tb', 'payroll_vs_gl', 'bank_vs_invoices', 'bank_vs_tb', 'service_recast', 'walkthrough',
        'sales_cutoff', 'purchase_cutoff', 'orderbook_vs_register',
        'gstr1_vs_sales_register', 'gstr3b_vs_gstr1', 'gstr3b_vs_books', 'rcm',
        'rpt_vs_books')),
    period VARCHAR NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    left_file_id INTEGER NOT NULL,
    right_file_id INTEGER,
    threshold DECIMAL(20, 2) NOT NULL,
    input_hash VARCHAR NOT NULL,
    unmapped INTEGER NOT NULL,
    as_of DATE,  -- bank_vs_invoices: the period's last day, the date open invoices are aged to
    due_days INTEGER,  -- bank_vs_invoices: invoice_due_days from config.toml at the run
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- The lines of a reconciliation, one per account (and one per unmapped ledger):
--   matched     both sides equal;
--   difference  both sides have a figure and they differ;
--   left_only   the trial balance has a balance for the account, the statements no figure;
--   right_only  the statements have a figure, no confirmed ledger rolls up to the account;
--   unmapped    a ledger not confirmed to an account (ledger; left_amount its debit - credit); flagged, not guessed;
--   fx          (registers) a foreign-currency invoice on both sides whose rupee amounts differ only by the ledger's
--               conversion rate (within the rate band): shown as FX gain or loss in note, listed, never queried, never
--               plugged;
--   fx          (bank_vs_invoices) a matched foreign-currency receipt whose booked rupee value (Ledger Vouchers) differs from its
--               invoice's Amount in Rs: left_amount the receipt's booked rupees, right_amount the invoice's, difference
--               the FX gain (positive) or loss; listed, never queried, never plugged.
--   not_reconciled  (gl_vs_tb) a balance sheet ledger with no prior trial balance to start from: listed;
--   undated     (service_recast) a sales invoice with no usable service dates, recognised on its invoice date: left_amount
--               is its amount; at or above the threshold it needs a query. service_recast lines: level 'period' (recognised
--               by the recast vs booked by invoice date: the period's revenue effect), 'month' and 'schedule' (per invoice,
--               its recognised and booked amounts in the period) are roll-ups, listed; level 'invoice' kind undated is queried.
-- difference = left_amount - right_amount (a missing side counts as 0). tb_vs_statements: a line at or above the
-- threshold gets a draft accountant query (query_id) once every ledger is mapped; one below it is listed only. The D4
-- reconciliations draft nothing: needs_query marks the lines Claude groups into issues (`fin query raise --items`:
-- query_id, and query_items), a difference, left_only or right_only line at a detail level (invoice, ledger, payroll)
-- at or above the threshold; the month and party lines are roll-ups, listed. Differences are queried, never fixed.
-- sources: file + sheet!row (trial balance) and file + sheet!cell or page.
-- item_id: a number for the line across runs (`fin query raise --items`). level and key: what a D4 line compares
-- ('month' 2024-05, 'party' CUST-0001, 'invoice' INV-1001, 'ledger' a ledger name, 'payroll' the period, 'gst' a return
-- month and measure, 2024-05 taxable_value, fin.reconcile.gst); note: how it was matched, or the fx rates.
-- bank_vs_invoices (level 'match' a receipt group and its invoices, 'receipt' an unmatched receipt, 'invoice' an unmatched
-- invoice; key the invoice numbers, the bank line or the invoice number): match_pass 'reference', 'party_amount_date',
-- 'amount_date' (fixed passes, fin.reconcile.bank_match) or 'confirmed' (a match Claude proposed and the user confirmed);
-- currency: the currency of left_amount and right_amount (NULL: rupees); amount_inr: the difference in rupees, what the
-- threshold is tested on (NULL when a foreign-currency line has no rupee value); note 'not_yet_due': an unmatched
-- invoice not yet past its due days at the period's last day (listed, not queried); 'no booked value': a foreign-currency
-- match with no Ledger Vouchers receipt to take the booked rupees from.
CREATE SEQUENCE recon_items_seq START 1;
CREATE TABLE recon_items (
    recon_id INTEGER NOT NULL,
    item_no INTEGER NOT NULL,
    item_id INTEGER NOT NULL UNIQUE DEFAULT nextval('recon_items_seq'),
    kind VARCHAR NOT NULL
        CHECK (kind IN ('matched', 'difference', 'left_only', 'right_only', 'unmapped', 'fx',
            'not_reconciled', 'undated')),
    level VARCHAR,
    key VARCHAR,
    needs_query BOOLEAN NOT NULL DEFAULT FALSE,
    note VARCHAR,
    match_pass VARCHAR,
    currency VARCHAR,
    amount_inr DECIMAL(20, 2),
    account VARCHAR,
    ledger VARCHAR,
    left_amount DECIMAL(20, 2),
    right_amount DECIMAL(20, 2),
    difference DECIMAL(20, 2) NOT NULL,
    left_source VARCHAR,
    right_source VARCHAR,
    query_id INTEGER,
    PRIMARY KEY (recon_id, item_no)
);

-- The lines an issue (an accountant query raised with `fin query raise --items`) groups: the query and each
-- recon_items.item_id in it. The count and value of each kind of difference in a query come from here.
CREATE TABLE query_items (
    query_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    PRIMARY KEY (query_id, item_id)
);

-- The bank lines and invoices behind each bank_vs_invoices item, with lineage (file_id + sheet + row). side 'bank' (a
-- receipt: bank_lines credit) or 'invoice' (register_lines, kind sales). party: the customer token. amount: in the
-- line's currency (a receipt's credit; an invoice's Amount, or Amount in Rs for INR); amount_inr: the invoice's Amount
-- in Rs, or the receipt's booked rupees from Ledger Vouchers (foreign currency; NULL when not found), the credit
-- itself for an INR receipt. Several lines share one item_no when a receipt pays several invoices or the reverse.
CREATE TABLE recon_match_lines (
    recon_id INTEGER NOT NULL,
    item_no INTEGER NOT NULL,
    side VARCHAR NOT NULL CHECK (side IN ('bank', 'invoice')),
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    party VARCHAR,
    line_date DATE NOT NULL,
    reference VARCHAR,
    currency VARCHAR NOT NULL,
    amount DECIMAL(20, 2) NOT NULL,
    amount_inr DECIMAL(20, 2),
    PRIMARY KEY (recon_id, item_no, side, file_id, sheet, "row")
);

-- Matches Claude proposed for what the fixed passes left unmatched (`fin reconcile <co> match --propose <file>`). One
-- row per proposed match: the bank lines and invoices it joins (lists of file_id, sheet, row). proposed -> confirmed or
-- rejected by the user (`fin review confirm|reject <co> <ids> --matches`); only confirmed ones are applied, as pass
-- 'confirmed', the next time bank_vs_invoices runs.
CREATE SEQUENCE match_proposals_seq START 1;
CREATE TABLE match_proposals (
    proposal_id INTEGER PRIMARY KEY DEFAULT nextval('match_proposals_seq'),
    engagement_id INTEGER NOT NULL,
    bank_lines VARCHAR NOT NULL,  -- JSON: [[file_id, sheet, row], ...]
    invoice_lines VARCHAR NOT NULL,  -- JSON: [[file_id, sheet, row], ...]
    confidence DOUBLE,
    reason VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
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
