-- Company database: contract coverage, samples and walkthroughs (docs/fdd.md F1). Run after diligence.sql (contracts,
-- engagement, passthrough_customers) and sources.sql (register_lines, source_files).

-- A sample of register lines for a walkthrough (`fin sample draw`): every line at or above the engagement threshold
-- (largest first, up to size), then the rest drawn at random. seed is stored so the same draw can be repeated.
-- population: the register lines of the kind and period; above_threshold: how many were at or above the threshold
-- (more than size means some were not sampled).
CREATE SEQUENCE samples_seq START 1;
CREATE TABLE samples (
    sample_id INTEGER PRIMARY KEY DEFAULT nextval('samples_seq'),
    engagement_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('sales', 'purchase')),
    period VARCHAR NOT NULL,
    size INTEGER NOT NULL,
    threshold DECIMAL(20, 2) NOT NULL,
    seed BIGINT NOT NULL,
    population INTEGER NOT NULL,
    above_threshold INTEGER NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- The lines of a sample, numbered from 1 in the order drawn, with their register lineage (file_id + sheet + row).
-- selection: 'threshold' (at or above the threshold) or 'random'. party is a token.
CREATE TABLE sample_lines (
    sample_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    selection VARCHAR NOT NULL CHECK (selection IN ('threshold', 'random')),
    party VARCHAR,
    invoice_no VARCHAR NOT NULL,
    invoice_date DATE NOT NULL,
    amount_inr DECIMAL(20, 2) NOT NULL,
    PRIMARY KEY (sample_id, line_no)
);

-- Claude's walkthrough of a sample line (`fin walkthrough record`): one row per check, with its evidence. check_name:
-- in_register (the line is in the register), contract_covers (a contract covers it and its rate, dates and value agree),
-- service_in_contract (the service period sits inside the contract), bank_matched (a bank receipt or payment is matched,
-- D5), ledger_posting (the ledger posting is found, D2). result: pass, exception or not_available. evidence says what
-- was seen; evidence_file and evidence_page where. read_by is 'claude': whether it is trusted is the user's decision.
-- One row per sample line and check (recording it again replaces the row).
CREATE SEQUENCE walkthroughs_seq START 1;
CREATE TABLE walkthroughs (
    walkthrough_id INTEGER PRIMARY KEY DEFAULT nextval('walkthroughs_seq'),
    sample_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    check_name VARCHAR NOT NULL
        CHECK (check_name IN ('in_register', 'contract_covers', 'service_in_contract', 'bank_matched', 'ledger_posting')),
    result VARCHAR NOT NULL CHECK (result IN ('pass', 'exception', 'not_available')),
    evidence VARCHAR NOT NULL,
    evidence_file VARCHAR,
    evidence_page INTEGER,
    read_by VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Per sample: its lines, how many have a walkthrough, the checks recorded and their results, and the lines with an
-- exception (exception_lines) with their amount (exception_amount, Amount in Rs added up without signs).
CREATE VIEW walkthrough_summary AS
SELECT
    s.sample_id, s.engagement_id, s.kind, s.period, s.size, s.seed,
    (SELECT count(*) FROM sample_lines l WHERE l.sample_id = s.sample_id) AS lines,
    (SELECT count(DISTINCT w.line_no) FROM walkthroughs w WHERE w.sample_id = s.sample_id) AS lines_walked,
    (SELECT count(*) FROM walkthroughs w WHERE w.sample_id = s.sample_id) AS checks,
    (SELECT count(*) FROM walkthroughs w WHERE w.sample_id = s.sample_id AND w.result = 'pass') AS checks_passed,
    (SELECT count(*) FROM walkthroughs w WHERE w.sample_id = s.sample_id AND w.result = 'exception') AS exceptions,
    (SELECT count(*) FROM walkthroughs w WHERE w.sample_id = s.sample_id AND w.result = 'not_available')
        AS not_available,
    (SELECT count(DISTINCT w.line_no) FROM walkthroughs w WHERE w.sample_id = s.sample_id AND w.result = 'exception')
        AS exception_lines,
    coalesce((SELECT sum(abs(l.amount_inr)) FROM sample_lines l WHERE l.sample_id = s.sample_id AND l.line_no IN (
        SELECT w.line_no FROM walkthroughs w WHERE w.sample_id = s.sample_id AND w.result = 'exception')), 0)
        AS exception_amount
FROM samples s
ORDER BY s.sample_id;

-- Contract coverage of the latest engagement's last annual period: every customer (core revenue, so the pass-through
-- customers are left out) and vendor (purchases) of its registers, ranked by Amount in Rs within party_kind (rank 1 is
-- the largest; `fin contracts coverage` shows the top `[fdd] coverage_top_n`). With the party's contract that ends
-- last (contracts_on_file counts them all): its dates and terms, as the user would ask:
--   contract_on_file     a contract of the party is loaded;
--   expired              it ended before the period end;
--   expires_within_12m   it ends from the period end to 12 months after it;
--   lock_in_left_months  whole months from the period end to the end of the lock-in (start + lock_in_months), 0 when
--                        over; NULL when the lock-in was not read.
-- period_end is the last day of the period under the engagement's fiscal start month.
CREATE VIEW contract_coverage AS
WITH latest AS (
    SELECT
        engagement_id,
        string_split(periods, ',')[-1] AS period,
        (make_date(2000 + CAST(substr(string_split(periods, ',')[-1], 3) AS INTEGER)
            - CASE WHEN fiscal_start_month > 1 THEN 1 ELSE 0 END, fiscal_start_month, 1)
            + INTERVAL 1 YEAR - INTERVAL 1 DAY)::DATE AS period_end
    FROM engagement ORDER BY engagement_id DESC LIMIT 1
),
lines AS (
    SELECT r.kind, r.customer, r.vendor, r.amount_inr, l.engagement_id, l.period, l.period_end
    FROM register_lines r
    JOIN source_files f ON f.file_id = r.file_id AND f.status = 'loaded'
    JOIN latest l ON l.engagement_id = f.engagement_id AND l.period = r.period
),
parties AS (
    SELECT engagement_id, period, period_end, 'customer' AS party_kind, customer AS counterparty,
        sum(amount_inr) AS amount_inr
    FROM lines
    WHERE kind = 'sales' AND customer IS NOT NULL
      AND customer NOT IN (SELECT p.customer FROM passthrough_customers p WHERE p.engagement_id = lines.engagement_id)
    GROUP BY ALL
    UNION ALL
    SELECT engagement_id, period, period_end, 'vendor', vendor, sum(amount_inr)
    FROM lines WHERE kind = 'purchase' AND vendor IS NOT NULL GROUP BY ALL
),
ranked AS (
    SELECT *, CAST(row_number() OVER (PARTITION BY party_kind ORDER BY amount_inr DESC, counterparty) AS INTEGER) AS rank
    FROM parties
),
contract_rows AS (
    SELECT c.*, count(*) OVER (PARTITION BY c.engagement_id, c.party_kind, c.counterparty) AS contracts_on_file,
        row_number() OVER (PARTITION BY c.engagement_id, c.party_kind, c.counterparty
            ORDER BY c.end_date DESC, c.contract_id DESC) AS pick
    FROM contracts c
)
SELECT
    r.engagement_id, r.period, r.period_end, r.party_kind, r.rank, r.counterparty, r.amount_inr,
    c.contract_id IS NOT NULL AS contract_on_file,
    coalesce(c.contracts_on_file, 0) AS contracts_on_file,
    c.contract_id, c.start_date, c.end_date,
    c.end_date < r.period_end AS expired,
    c.end_date >= r.period_end AND c.end_date <= (r.period_end + INTERVAL 12 MONTH)::DATE AS expires_within_12m,
    c.auto_renew, c.change_of_control, c.lock_in_months,
    CASE WHEN c.lock_in_months IS NOT NULL THEN greatest(0, date_sub('month', r.period_end,
        (c.start_date + to_months(c.lock_in_months))::DATE)) END AS lock_in_left_months,
    c.notice_days, c.file_name, c.page
FROM ranked r
LEFT JOIN contract_rows c ON c.pick = 1 AND c.engagement_id = r.engagement_id
    AND c.party_kind = r.party_kind AND c.counterparty = r.counterparty
ORDER BY r.party_kind, r.rank;
