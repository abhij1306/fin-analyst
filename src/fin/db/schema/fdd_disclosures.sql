-- Company database: FDD F5, contingencies, commitments, notices and related parties (docs/fdd.md F5,
-- fin.disclosures). Run after sources.sql. Money is DECIMAL(20,2) rupees. Claude's readings of notes to accounts and
-- legal and tax notices load from a reading CSV (`fin disclosures load`), file + page per row, read_by 'ai:claude':
-- fin does not check them; trusting one is the user's call.

-- One row per document a reading was loaded from. file_id comes from source_files_seq, so it never equals a
-- source file's id.
CREATE TABLE disclosure_files (
    file_id INTEGER PRIMARY KEY DEFAULT nextval('source_files_seq'),
    engagement_id INTEGER NOT NULL,
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    reading_name VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    loaded_at TIMESTAMP NOT NULL,
    UNIQUE (engagement_id, sha256)
);

-- One row per item Claude read. kind (the schedule it is on):
--   contingent_liability, commitment, guarantee, litigation, tax_matter, off_balance_sheet,
--   related_party (a party and its relationship), related_party_transaction, related_party_balance.
-- counterparty: a register customer or vendor's token (CUST-, VEND-), else a loaded ledger's name as stored, else
-- the name as read; related_party_matches matches it to the customers, vendors and ledgers.
-- nature and relationship: as read. amount: rupees (the reading's
-- unit applied). forum and stage (litigation, tax matters); assessment_year, demand, paid_under_protest (tax matters).
-- books (related_party_transaction only): what rpt_vs_books compares the amount with: 'sales' (the sales register of
-- the customers linked to the party), 'purchases' (the purchase register of its vendors), 'debit' or 'credit' (the
-- period's Ledger Vouchers debits or credits on `ledger`, the ledger as stored). NULL: not compared.
CREATE SEQUENCE disclosures_seq START 1;
CREATE TABLE disclosures (
    disclosure_id INTEGER PRIMARY KEY DEFAULT nextval('disclosures_seq'),
    engagement_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('contingent_liability', 'commitment', 'guarantee', 'litigation',
        'tax_matter', 'off_balance_sheet', 'related_party', 'related_party_transaction', 'related_party_balance')),
    period VARCHAR NOT NULL,
    counterparty VARCHAR,
    nature VARCHAR NOT NULL,
    amount DECIMAL(20, 2),
    forum VARCHAR,
    stage VARCHAR,
    assessment_year VARCHAR,
    demand DECIMAL(20, 2),
    paid_under_protest DECIMAL(20, 2),
    relationship VARCHAR,
    books VARCHAR CHECK (books IN ('sales', 'purchases', 'debit', 'credit')),
    ledger VARCHAR,
    page INTEGER NOT NULL,
    read_by VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- Links Claude proposed between a related party and a customer, vendor or ledger that fin did not
-- match (`fin disclosures related <co> <file.json>`). proposed -> confirmed or rejected by the user
-- (`fin review confirm|reject <co> <ids> --related`); only confirmed ones count.
CREATE SEQUENCE related_party_links_seq START 1;
CREATE TABLE related_party_links (
    link_id INTEGER PRIMARY KEY DEFAULT nextval('related_party_links_seq'),
    engagement_id INTEGER NOT NULL,
    party VARCHAR NOT NULL,
    link_kind VARCHAR NOT NULL CHECK (link_kind IN ('customer', 'vendor', 'ledger')),
    target VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    confidence DOUBLE,
    reason VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL,
    UNIQUE (engagement_id, party, link_kind, target)
);

-- The related parties of each engagement: the counterparties of its related_party* rows.
CREATE VIEW related_parties AS
SELECT DISTINCT engagement_id, counterparty AS party
FROM disclosures
WHERE kind IN ('related_party', 'related_party_transaction', 'related_party_balance') AND counterparty IS NOT NULL;

-- What each related party is in the books: 'matched' (fin: the party is a sales customer or a purchase vendor token,
-- or a trial balance or Ledger Vouchers ledger of the engagement's loaded files; a ledger is stored by name, so it
-- matches a token through the masked name it is, ignoring case and spaces) or 'confirmed' (a link the user confirmed).
CREATE VIEW related_party_matches AS
WITH files AS (
    SELECT file_id, engagement_id, kind FROM source_files WHERE status = 'loaded'
),
ledgers AS (
    SELECT f.engagement_id, t.ledger FROM trial_balance t JOIN files f USING (file_id) WHERE t.row_type = 'ledger'
    UNION
    SELECT f.engagement_id, b.ledger FROM ledger_blocks b JOIN files f USING (file_id)
),
targets AS (
    SELECT DISTINCT f.engagement_id, 'customer' AS link_kind, r.customer AS target, r.customer AS party
    FROM register_lines r JOIN files f USING (file_id) WHERE r.kind = 'sales' AND r.customer IS NOT NULL
    UNION
    SELECT DISTINCT f.engagement_id, 'vendor', r.vendor, r.vendor
    FROM register_lines r JOIN files f USING (file_id) WHERE r.kind = 'purchase' AND r.vendor IS NOT NULL
    UNION
    SELECT l.engagement_id, 'ledger', l.ledger, coalesce(k.token, l.ledger)
    FROM ledgers l LEFT JOIN mask_values k ON k.kind IN ('customer', 'vendor')
        AND k.match_key = lower(regexp_replace(trim(l.ledger), '\s+', ' ', 'g'))
)
SELECT p.engagement_id, p.party, t.link_kind, t.target, 'matched' AS status
FROM related_parties p JOIN targets t ON t.engagement_id = p.engagement_id AND t.party = p.party
UNION
SELECT l.engagement_id, l.party, l.link_kind, l.target, 'confirmed'
FROM related_party_links l JOIN related_parties p ON p.engagement_id = l.engagement_id AND p.party = l.party
WHERE l.status = 'confirmed';

-- What the books show with each related party, per period: source 'sales_register' or 'purchase_register' (amount:
-- Amount in Rs before GST of the linked customers' or vendors' lines, by the line's period) or 'ledger_vouchers' (the
-- linked ledgers' entries of the period's loaded Ledger Vouchers, per voucher type as nature: debit, credit, and
-- amount = debit - credit).
CREATE VIEW related_party_transactions AS
SELECT m.engagement_id, m.party, r.period, 'sales_register' AS source, 'sales' AS nature,
    NULL::DECIMAL(20, 2) AS debit, NULL::DECIMAL(20, 2) AS credit, sum(r.amount_inr) AS amount, count(*) AS lines
FROM related_party_matches m
JOIN source_files f ON f.engagement_id = m.engagement_id AND f.kind = 'registers' AND f.status = 'loaded'
JOIN register_lines r ON r.file_id = f.file_id AND r.kind = 'sales' AND r.customer = m.target
WHERE m.link_kind = 'customer' AND r.period IS NOT NULL
GROUP BY ALL
UNION ALL
SELECT m.engagement_id, m.party, r.period, 'purchase_register', 'purchases', NULL, NULL, sum(r.amount_inr), count(*)
FROM related_party_matches m
JOIN source_files f ON f.engagement_id = m.engagement_id AND f.kind = 'registers' AND f.status = 'loaded'
JOIN register_lines r ON r.file_id = f.file_id AND r.kind = 'purchase' AND r.vendor = m.target
WHERE m.link_kind = 'vendor' AND r.period IS NOT NULL
GROUP BY ALL
UNION ALL
SELECT m.engagement_id, m.party, f.period, 'ledger_vouchers', coalesce(e.voucher_type, '(no voucher type)'),
    sum(e.debit), sum(e.credit), sum(e.debit) - sum(e.credit), count(*)
FROM related_party_matches m
JOIN source_files f ON f.engagement_id = m.engagement_id AND f.kind = 'ledger_vouchers' AND f.status = 'loaded'
JOIN ledger_entries e ON e.file_id = f.file_id AND e.ledger = m.target
WHERE m.link_kind = 'ledger'
GROUP BY ALL;

-- A related party's average rate per product against the other customers' (sales) or vendors' (purchases), per period:
-- rate = Amount in Rs / quantity over the register lines with a product and a quantity above 0. difference_pct =
-- (party rate - others' rate) / others' rate x 100; NULL when no other party bought or sold the product.
CREATE VIEW related_party_pricing AS
WITH lines AS (
    SELECT f.engagement_id, r.kind, r.period, r.product, r.quantity, r.amount_inr,
        CASE r.kind WHEN 'sales' THEN r.customer ELSE r.vendor END AS target,
        CASE r.kind WHEN 'sales' THEN 'customer' ELSE 'vendor' END AS link_kind
    FROM register_lines r JOIN source_files f ON f.file_id = r.file_id AND f.kind = 'registers' AND f.status = 'loaded'
    WHERE r.product IS NOT NULL AND r.quantity > 0 AND r.period IS NOT NULL
),
party AS (
    SELECT m.party, l.engagement_id, l.kind, l.period, l.product, sum(l.quantity) AS quantity,
        sum(l.amount_inr) AS amount
    FROM lines l JOIN related_party_matches m
        ON m.engagement_id = l.engagement_id AND m.link_kind = l.link_kind AND m.target = l.target
    GROUP BY ALL
),
others AS (
    SELECT l.engagement_id, l.kind, l.period, l.product, sum(l.quantity) AS quantity, sum(l.amount_inr) AS amount
    FROM lines l
    WHERE NOT EXISTS (
        SELECT 1 FROM related_party_matches m
        WHERE m.engagement_id = l.engagement_id AND m.link_kind = l.link_kind AND m.target = l.target
    )
    GROUP BY ALL
)
SELECT p.engagement_id, p.party, p.kind, p.period, p.product, p.quantity, p.amount,
    round(p.amount / p.quantity, 2) AS party_rate,
    o.quantity AS others_quantity,
    round(o.amount / o.quantity, 2) AS others_rate,
    round((p.amount / p.quantity - o.amount / o.quantity) / (o.amount / o.quantity) * 100, 2) AS difference_pct
FROM party p
LEFT JOIN others o
    ON o.engagement_id = p.engagement_id AND o.kind = p.kind AND o.period = p.period AND o.product = p.product
    AND o.quantity > 0 AND o.amount <> 0;

-- Loans with related parties: each ledger linked to a party (matched or confirmed) whose confirmed account is a loan
-- or a borrowing, per period with a loaded trial balance. closing: debit - credit at the period end (positive: a loan
-- given; negative: a borrowing); opening: the previous period's trial balance closing (NULL when none is loaded);
-- average_balance: (opening + closing) / 2, or the closing alone (average_basis says which). interest: the period's
-- Ledger Vouchers entries on the ledger whose other ledger (particulars) is confirmed to finance_cost,
-- interest_expended, other_income or interest_income, netted (|debit - credit|, so a reversed accrual cancels); NULL
-- when the period has no loaded Ledger Vouchers (not known, not zero). implied_rate_pct: interest / |average| x 100.
CREATE VIEW related_party_loans AS
WITH tb AS (
    SELECT f.engagement_id, f.period, f.period_start, f.period_end, t.ledger,
        sum(t.closing_debit - t.closing_credit) AS closing
    FROM trial_balance t
    JOIN source_files f ON f.file_id = t.file_id AND f.kind = 'trial_balance' AND f.status = 'loaded'
    WHERE t.row_type = 'ledger'
    GROUP BY ALL
),
loans AS (
    SELECT m.engagement_id, m.party, m.target AS ledger, a.account
    FROM related_party_matches m
    JOIN ledger_accounts a ON a.ledger = m.target AND a.status = 'confirmed'
    WHERE m.link_kind = 'ledger' AND a.account IN ('loans_non_current', 'loans_current', 'loans', 'lt_borrowings',
        'st_borrowings', 'borrowings')
),
interest AS (
    SELECT f.engagement_id, f.period, e.ledger, abs(sum(e.debit - e.credit)) AS interest
    FROM ledger_entries e
    JOIN source_files f ON f.file_id = e.file_id AND f.kind = 'ledger_vouchers' AND f.status = 'loaded'
    JOIN ledger_accounts a ON a.ledger = e.particulars AND a.status = 'confirmed'
        AND a.account IN ('finance_cost', 'interest_expended', 'other_income', 'interest_income')
    GROUP BY ALL
),
vouchers AS (
    SELECT DISTINCT engagement_id, period FROM source_files WHERE kind = 'ledger_vouchers' AND status = 'loaded'
),
balances AS (
    SELECT l.engagement_id, l.party, l.ledger, l.account, c.period, c.closing,
        (SELECT p.closing FROM tb p WHERE p.engagement_id = c.engagement_id AND p.ledger = c.ledger
            AND p.period_end = c.period_start - INTERVAL 1 DAY) AS opening
    FROM loans l JOIN tb c ON c.engagement_id = l.engagement_id AND c.ledger = l.ledger
),
rated AS (
    SELECT b.*, CASE WHEN v.period IS NULL THEN NULL ELSE coalesce(i.interest, 0) END AS interest
    FROM balances b
    LEFT JOIN vouchers v ON v.engagement_id = b.engagement_id AND v.period = b.period
    LEFT JOIN interest i ON i.engagement_id = b.engagement_id AND i.period = b.period AND i.ledger = b.ledger
)
SELECT b.engagement_id, b.party, b.ledger, b.account, b.period, b.opening, b.closing,
    CASE WHEN b.opening IS NULL THEN b.closing ELSE (b.opening + b.closing) / 2 END AS average_balance,
    CASE WHEN b.opening IS NULL THEN 'closing only' ELSE 'opening and closing' END AS average_basis,
    b.interest,
    CASE WHEN b.opening IS NULL AND b.closing <> 0 THEN round(b.interest / abs(b.closing) * 100, 2)
        WHEN b.opening IS NOT NULL AND b.opening + b.closing <> 0
            THEN round(b.interest / abs((b.opening + b.closing) / 2) * 100, 2)
    END AS implied_rate_pct
FROM rated b;
