-- Company database, companies/<company>.duckdb: the diligence tables (docs/diligence.md, fin.diligence). Run
-- after company.sql and company_views.sql. Money is DECIMAL(20,2) rupees. No foreign keys (see company.sql): the id
-- columns are the links. Reported facts are never changed here; adjustments sit beside them.
-- Every row added and every status change is also appended to the audit table diligence_events.

CREATE SEQUENCE engagement_seq START 1;
CREATE SEQUENCE accountant_queries_seq START 1;
CREATE SEQUENCE adjustments_seq START 1;

-- One diligence job on the company. At most one is open at a time (fin.diligence.engagement checks it).
--   open    `fin engagement open`; the diligence commands work on it;
--   closed  `fin engagement close`; kept with its queries and adjustments.
-- periods: the annual periods in scope, comma-separated in order, e.g. 'FY24,FY25,FY26'.
-- query_threshold: a difference at or above this many rupees becomes a query (config.toml [diligence], or
-- `--threshold`), fixed when the engagement opens.
-- stub: the optional year-to-date period after the last annual one (`--stub FY27-YTD-SEP`), from stub_start (the
-- fiscal year's first day) to stub_end (the named month's last day). Its figures are unaudited management accounts.
CREATE TABLE engagement (
    engagement_id INTEGER PRIMARY KEY DEFAULT nextval('engagement_seq'),
    periods VARCHAR NOT NULL,
    query_threshold DECIMAL(20, 2) NOT NULL,
    stub VARCHAR,
    stub_start DATE,
    stub_end DATE,
    -- bank matching settings copied from config.toml [diligence] when the engagement opens (like query_threshold)
    match_window_days INTEGER NOT NULL DEFAULT 60,
    fx_window_days INTEGER NOT NULL DEFAULT 7,
    invoice_due_days INTEGER NOT NULL DEFAULT 30,
    fiscal_start_month INTEGER NOT NULL DEFAULT 4,
    -- FDD cut-off windows (docs/fdd.md F3) copied from config.toml [fdd] when the engagement opens
    cutoff_days INTEGER NOT NULL DEFAULT 15,
    unrecorded_days INTEGER NOT NULL DEFAULT 60,
    status VARCHAR NOT NULL CHECK (status IN ('open', 'closed')),
    opened_run_id VARCHAR NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    closed_run_id VARCHAR,
    closed_at TIMESTAMP
);

-- Questions for the company's accountant. Claude drafts them; the user approves them; fin never sends anything.
--   draft      raised (`fin query raise`); the wording can still change (`fin query edit`);
--   approved   the user approved the wording (`fin query approve`);
--   sent       the user says they sent it (`fin query sent`);
--   answered   the accountant's answer is recorded (`fin query answer`): answer, answer_source;
--   closed     the answer settles it (`fin query close`);
--   withdrawn  dropped before it was closed (`fin query withdraw`), with the reason in note.
-- origin: 'finding' (raised from findings.finding_id: period, scope, difference and sources copied from it; the
-- finding itself is not changed), 'recon' (drafted by a reconciliation for a line at or above the threshold; the
-- line's recon_items.query_id links it) or 'manual'. amount: the difference at issue in rupees, NULL when there is
-- none.
-- sources: where the figures are, as file + sheet!cell or page references, '; '-separated.
-- status_run_id / status_at: the run and time of the last status change (or of the raise).
CREATE TABLE accountant_queries (
    query_id INTEGER PRIMARY KEY DEFAULT nextval('accountant_queries_seq'),
    engagement_id INTEGER NOT NULL,
    origin VARCHAR NOT NULL CHECK (origin IN ('finding', 'recon', 'manual')),
    finding_id BIGINT,
    period VARCHAR,
    scope VARCHAR CHECK (scope IN ('standalone', 'consolidated')),
    question VARCHAR NOT NULL,
    amount DECIMAL(20, 2),
    sources VARCHAR NOT NULL,
    status VARCHAR NOT NULL
        CHECK (status IN ('draft', 'approved', 'sent', 'answered', 'closed', 'withdrawn')),
    answer VARCHAR,
    answer_source VARCHAR,
    created_run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL,
    note VARCHAR
);

-- Proposed adjustments to EBITDA, revenue and costs. Reported facts are never changed; adjusted figures are reported +
-- accepted adjustments.
--   proposed  `fin adjust propose`, by Claude or the user;
--   accepted  the user accepted it (`fin adjust accept`);
--   rejected  the user rejected it (`fin adjust reject`), with the reason.
-- period: an annual period of the engagement. category: one of config.toml [diligence] adjustment_categories.
-- account: the dictionary account it relates to (optional). Effects, signed rupees: revenue_effect (positive raises
-- revenue), cost_effect (positive raises costs) and amount, the effect on EBITDA, which is always
-- revenue_effect - cost_effect (checked). A pass-through normalisation strips revenue and its cost: both negative.
-- A reclassification between two cost accounts has no net effect (all three 0): it names the account the cost moves
-- to (account) and the one it leaves (from_account), and moved_amount, the positive amount moved; from_account and
-- moved_amount are set together or not at all.
-- Evidence: query_id, finding_id or source_ref (file + sheet!cell or page); at least one is set.
CREATE TABLE adjustments (
    adj_id INTEGER PRIMARY KEY DEFAULT nextval('adjustments_seq'),
    engagement_id INTEGER NOT NULL,
    period VARCHAR NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    category VARCHAR NOT NULL,
    account VARCHAR,
    amount DECIMAL(20, 2) NOT NULL,
    revenue_effect DECIMAL(20, 2) NOT NULL,
    cost_effect DECIMAL(20, 2) NOT NULL,
    from_account VARCHAR,
    moved_amount DECIMAL(20, 2),
    description VARCHAR NOT NULL,
    query_id INTEGER,
    finding_id BIGINT,
    source_ref VARCHAR,
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'accepted', 'rejected')),
    reason VARCHAR,
    created_run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL,
    CHECK (query_id IS NOT NULL OR finding_id IS NOT NULL OR source_ref IS NOT NULL),
    CHECK (amount = revenue_effect - cost_effect),
    CHECK ((from_account IS NULL) = (moved_amount IS NULL))
);

-- The pass-through customers of an engagement (`fin engagement passthrough`): the SI/reseller customers, as tokens,
-- whose deals pass through at roughly zero margin. The list is the user's; source says where it came from. Setting
-- the list again replaces it (each setting is in diligence_events). The views revenue_split and passthrough_lines
-- (diligence_split_views.sql) use the latest engagement's list.
CREATE TABLE passthrough_customers (
    engagement_id INTEGER NOT NULL,
    customer VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (engagement_id, customer)
);

-- Gate approvals (fin.diligence.gates), append-only: one row each time the user approves a gate
-- (`fin gate approve`); rows are never changed. A gate is approved while the hash of its inputs now equals input_hash
-- of its latest approval; when the data under it changes, it reopens on its own.
-- gate: 'G1', 'G2', 'G3', 'G4'. input_hash: sha256 of the gate's inputs at approval; input_hashes:
-- JSON {input name: sha256} of each input (to say which changed). checklist: JSON [{"ok": true, "text": ...}], the
-- checklist as it was at approval (every line passed).
CREATE SEQUENCE gate_approvals_seq START 1;
CREATE TABLE gate_approvals (
    approval_id INTEGER PRIMARY KEY DEFAULT nextval('gate_approvals_seq'),
    engagement_id INTEGER NOT NULL,
    gate VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL,
    input_hashes VARCHAR NOT NULL,
    checklist VARCHAR NOT NULL,
    approved_run_id VARCHAR NOT NULL,
    approved_at TIMESTAMP NOT NULL
);

-- Contracts Claude read from a contract document (`fin recast contracts`, a reading CSV with file + page): optional
-- evidence for the service-period recast, never the source of its schedule, and the contract terms of the FDD
-- extension (docs/fdd.md F1). party_kind: 'customer' or 'vendor'; counterparty is its token (CUST-0001, VEND-0001);
-- invoice_no links the contract to a sales (customer) or purchase (vendor) invoice when the reading names one.
-- value: the contract value as read, in rupees (NULL when not read); billing_terms, sla_terms, penalty_terms,
-- escalation_terms and renewal_terms: as read, names replaced by tokens. The terms are NULL when the reading did not
-- say (not read), which is not the same as no clause: lock_in_months and notice_days (whole numbers), escalation_pct
-- (a year), and the yes/no clauses auto_renew, termination_convenience (termination for convenience),
-- change_of_control and exclusivity. file_name, sha256 and page say where it was read; read_by is 'claude'.
CREATE SEQUENCE contracts_seq START 1;
CREATE TABLE contracts (
    contract_id INTEGER PRIMARY KEY DEFAULT nextval('contracts_seq'),
    engagement_id INTEGER NOT NULL,
    party_kind VARCHAR NOT NULL DEFAULT 'customer' CHECK (party_kind IN ('customer', 'vendor')),
    counterparty VARCHAR NOT NULL,
    invoice_no VARCHAR,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    value DECIMAL(20, 2),
    billing_terms VARCHAR,
    lock_in_months INTEGER,
    notice_days INTEGER,
    sla_terms VARCHAR,
    penalty_terms VARCHAR,
    escalation_terms VARCHAR,
    escalation_pct DECIMAL(7, 2),
    renewal_terms VARCHAR,
    auto_renew BOOLEAN,
    termination_convenience BOOLEAN,
    change_of_control BOOLEAN,
    exclusivity BOOLEAN,
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    page INTEGER NOT NULL,
    read_by VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);
