-- Company database: the reconciliations and what they found (fin.diligence.recon, fin.reconcile). Run after
-- sources.sql. Money is DECIMAL(20,2) rupees. No foreign keys: file_id, recon_id and query_id are the link.

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
    due_days INTEGER,  -- bank_vs_invoices: the engagement's invoice_due_days (credit terms) at the run
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
    party VARCHAR,  -- register invoice lines: the invoice's customer or vendor token (the books' party for a voucher)
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
