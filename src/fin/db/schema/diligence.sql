-- Company database, companies/<company>.duckdb: the diligence tables (docs/DILIGENCE_PLAN.md, fin.diligence). Run
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
CREATE TABLE engagement (
    engagement_id INTEGER PRIMARY KEY DEFAULT nextval('engagement_seq'),
    periods VARCHAR NOT NULL,
    query_threshold DECIMAL(20, 2) NOT NULL,
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
-- finding itself is not changed) or 'manual'. amount: the difference at issue in rupees, NULL when there is none.
-- sources: where the figures are, as file + sheet!cell or page references, '; '-separated.
-- status_run_id / status_at: the run and time of the last status change (or of the raise).
CREATE TABLE accountant_queries (
    query_id INTEGER PRIMARY KEY DEFAULT nextval('accountant_queries_seq'),
    engagement_id INTEGER NOT NULL,
    origin VARCHAR NOT NULL CHECK (origin IN ('finding', 'manual')),
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

-- Proposed adjustments to EBITDA. Reported facts are never changed; adjusted figures are reported + accepted
-- adjustments.
--   proposed  `fin adjust propose`, by Claude or the user;
--   accepted  the user accepted it (`fin adjust accept`);
--   rejected  the user rejected it (`fin adjust reject`), with the reason.
-- period: an annual period of the engagement. category: one of config.toml [diligence] adjustment_categories.
-- account: the dictionary account it relates to (optional). amount: the signed effect on EBITDA in rupees (positive
-- raises EBITDA). Evidence: query_id, finding_id or source_ref (file + sheet!cell or page); at least one is set.
CREATE TABLE adjustments (
    adj_id INTEGER PRIMARY KEY DEFAULT nextval('adjustments_seq'),
    engagement_id INTEGER NOT NULL,
    period VARCHAR NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    category VARCHAR NOT NULL,
    account VARCHAR,
    amount DECIMAL(20, 2) NOT NULL,
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
    CHECK (query_id IS NOT NULL OR finding_id IS NOT NULL OR source_ref IS NOT NULL)
);
