-- Company database: FDD F4, net debt and debt-like items (docs/fdd.md F4, docs/decisions/fdd-f4.md). Run after the
-- diligence and source files (it reads engagement, source_files, trial_balance and ledger_accounts). Money is rupees.
-- Nothing here changes company data: the views read the trial balances as filed.

-- The net debt class of a trial balance ledger, where it is not fin's default (netdebt_default_accounts): Claude
-- proposes one with a reason (`fin netdebt propose`) and the user confirms or rejects it
-- (`fin review confirm|reject <co> <ids> --netdebt`); only a confirmed class counts, and it wins over the default.
-- One row per ledger (the token for a party ledger), for every period end; a new proposal on a rejected ledger
-- replaces it.
--   cash       cash and bank balances: lower net debt;
--   debt       borrowings and lease liabilities;
--   debt_like  other liabilities a buyer treats as debt (employee benefit provisions, overdue statutory dues, income
--              tax payable, capex creditors, deferred or contingent consideration, unpaid dividends, ...);
--   none       not part of net debt.
CREATE SEQUENCE netdebt_classes_seq START 1;
CREATE TABLE netdebt_classes (
    class_id INTEGER PRIMARY KEY DEFAULT nextval('netdebt_classes_seq'),
    ledger VARCHAR NOT NULL UNIQUE,
    debt_class VARCHAR NOT NULL CHECK (debt_class IN ('cash', 'debt', 'debt_like', 'none')),
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    reason VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,  -- the run of the last change
    changed_at TIMESTAMP NOT NULL
);

-- Debt-like items not in a ledger (an indicative gratuity shortfall, a probable contingency, a cut-off item):
-- `fin netdebt item propose`, by Claude or the user, with evidence (a reference: a query, a fin output, file + page);
-- the user accepts or rejects it (`fin netdebt item accept|reject`), as for adjustments. amount: signed rupees,
-- positive raises net debt. period: a year of the engagement or its stub. reason: why it was rejected.
CREATE SEQUENCE netdebt_items_seq START 1;
CREATE TABLE netdebt_items (
    item_id INTEGER PRIMARY KEY DEFAULT nextval('netdebt_items_seq'),
    engagement_id INTEGER NOT NULL,
    period VARCHAR NOT NULL,
    amount DECIMAL(20, 2) NOT NULL CHECK (amount <> 0),
    description VARCHAR NOT NULL,
    evidence VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'accepted', 'rejected')),
    reason VARCHAR,
    created_run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL
);

-- fin's default class for a ledger confirmed to one of these accounts (ledger_accounts). A ledger confirmed to a profit
-- and loss account defaults to 'none'; every other ledger is unclassified until the user confirms a class. Other bank
-- balances (fixed deposits, margin money, lien-marked balances) have no default: a buyer often treats restricted cash
-- apart, so the user confirms their class.
CREATE VIEW netdebt_default_accounts AS
SELECT * FROM (VALUES
    ('cash', 'cash'),
    ('cash_and_bank_balances', 'cash'),
    ('lt_borrowings', 'debt'),
    ('st_borrowings', 'debt'),
    ('borrowings', 'debt'),
    ('debt_securities', 'debt'),
    ('subordinated_liabilities', 'debt'),
    ('lease_liabilities_non_current', 'debt'),
    ('lease_liabilities_current', 'debt'),
    ('lease_liabilities', 'debt')
) AS t(account, debt_class);

-- Each ledger of the latest engagement's loaded trial balances with its class: the confirmed one (class_source
-- 'confirmed'), else the default from its confirmed account ('default'), else NULL (unclassified). proposal_id and
-- proposed_class: a proposal waiting for the user (not applied).
CREATE VIEW netdebt_ledger_classes AS
WITH ledgers AS (
    SELECT t.ledger, arg_max(t.group_name, t.file_id) AS group_name
    FROM trial_balance t JOIN source_files s USING (file_id)
    WHERE s.kind = 'trial_balance' AND s.status = 'loaded' AND t.row_type = 'ledger'
        AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
    GROUP BY t.ledger
)
SELECT
    l.ledger, l.group_name, m.account, m.statement,
    coalesce(c.debt_class, d.debt_class, CASE WHEN m.statement = 'profit_and_loss' THEN 'none' END) AS debt_class,
    CASE
        WHEN c.ledger IS NOT NULL THEN 'confirmed'
        WHEN d.debt_class IS NOT NULL OR m.statement = 'profit_and_loss' THEN 'default'
    END AS class_source,
    p.class_id AS proposal_id, p.debt_class AS proposed_class
FROM ledgers l
LEFT JOIN ledger_accounts m ON m.ledger = l.ledger AND m.status = 'confirmed'
LEFT JOIN netdebt_default_accounts d ON d.account = m.account
LEFT JOIN netdebt_classes c ON c.ledger = l.ledger AND c.status = 'confirmed'
LEFT JOIN netdebt_classes p ON p.ledger = l.ledger AND p.status = 'proposed';

-- What net debt adds up, per period of the latest engagement: each cash, debt and debt-like ledger's closing balance
-- in the period's loaded trial balance (line_kind 'ledger'; cash as debit less credit, debt and debt-like as credit
-- less debit, so each reads positive in the usual case and an overdrawn bank reads as negative cash), and each
-- proposed or accepted debt-like item (line_kind 'item'). applied: counted in net_debt (ledgers and accepted items);
-- a proposed item is shown, not applied. source: file + sheet!row, or the item's evidence.
CREATE VIEW net_debt_lines AS
WITH tb AS (
    SELECT s.engagement_id, s.period, s.basis, s.period_end, s.file_id, s.file_name FROM source_files s
    WHERE s.kind = 'trial_balance' AND s.status = 'loaded'
        AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
)
SELECT
    tb.engagement_id, tb.period, tb.basis, tb.period_end, 'ledger' AS line_kind, c.debt_class, c.class_source,
    t.ledger, c.account, NULL::INTEGER AS item_id, NULL::VARCHAR AS description,
    CAST(CASE WHEN c.debt_class = 'cash' THEN t.closing_debit - t.closing_credit
        ELSE t.closing_credit - t.closing_debit END AS DECIMAL(38, 2)) AS amount,
    TRUE AS applied, 'applied' AS status,
    tb.file_name || ' ' || t.sheet || '!' || t."row" AS source
FROM tb
JOIN trial_balance t ON t.file_id = tb.file_id AND t.row_type = 'ledger'
JOIN netdebt_ledger_classes c ON c.ledger = t.ledger
WHERE c.debt_class IN ('cash', 'debt', 'debt_like')
UNION ALL
SELECT
    i.engagement_id, i.period, CASE WHEN i.period = e.stub THEN 'unaudited' ELSE 'audited' END, tb.period_end,
    'item', 'debt_like', NULL, NULL, NULL, i.item_id, i.description, CAST(i.amount AS DECIMAL(38, 2)),
    i.status = 'accepted', i.status, i.evidence
FROM netdebt_items i
JOIN engagement e ON e.engagement_id = i.engagement_id
LEFT JOIN tb ON tb.period = i.period
WHERE i.status IN ('proposed', 'accepted') AND i.engagement_id = (SELECT max(engagement_id) FROM engagement);

-- Net debt at each period end of the latest engagement (its annual periods, then the stub: unaudited management
-- accounts). net_debt = debt + debt_like - cash; debt_like = debt-like ledgers + accepted items. The ledger figures
-- are NULL when the period has no loaded trial balance (never 0). proposed_items: the sum of the proposed items, not
-- applied. unclassified_ledgers: the trial balance's ledgers with no class (while any is unclassified, net debt may be
-- incomplete); proposed_classes: those with a class proposal waiting for the user.
CREATE VIEW net_debt AS
WITH e AS (SELECT * FROM engagement WHERE engagement_id = (SELECT max(engagement_id) FROM engagement)),
periods AS (
    SELECT engagement_id, unnest(string_split(periods, ',')) AS period, 'audited' AS basis, 1 AS n FROM e
    UNION ALL
    SELECT engagement_id, stub, 'unaudited', 2 FROM e WHERE stub IS NOT NULL
),
tb AS (
    SELECT s.period, s.period_end, s.file_id, s.file_name FROM source_files s
    WHERE s.kind = 'trial_balance' AND s.status = 'loaded' AND s.engagement_id = (SELECT engagement_id FROM e)
),
sums AS (
    SELECT period,
        sum(amount) FILTER (WHERE line_kind = 'ledger' AND debt_class = 'cash') AS cash,
        sum(amount) FILTER (WHERE line_kind = 'ledger' AND debt_class = 'debt') AS debt,
        sum(amount) FILTER (WHERE line_kind = 'ledger' AND debt_class = 'debt_like') AS debt_like_ledgers,
        sum(amount) FILTER (WHERE line_kind = 'item' AND applied) AS debt_like_items,
        sum(amount) FILTER (WHERE line_kind = 'item' AND NOT applied) AS proposed_items,
        count(*) FILTER (WHERE line_kind = 'item' AND NOT applied) AS proposed_item_count
    FROM net_debt_lines GROUP BY period
),
coverage AS (
    SELECT tb.period,
        count(*) FILTER (WHERE c.debt_class IS NULL) AS unclassified_ledgers,
        count(*) FILTER (WHERE c.proposal_id IS NOT NULL) AS proposed_classes
    FROM tb
    JOIN trial_balance t ON t.file_id = tb.file_id AND t.row_type = 'ledger'
    JOIN netdebt_ledger_classes c ON c.ledger = t.ledger
    GROUP BY tb.period
),
figures AS (
    SELECT p.engagement_id, p.period, p.basis, p.n, tb.period_end, tb.file_name,
        CASE WHEN tb.file_id IS NOT NULL THEN CAST(coalesce(s.cash, 0) AS DECIMAL(38, 2)) END AS cash,
        CASE WHEN tb.file_id IS NOT NULL THEN CAST(coalesce(s.debt, 0) AS DECIMAL(38, 2)) END AS debt,
        CASE WHEN tb.file_id IS NOT NULL THEN CAST(coalesce(s.debt_like_ledgers, 0) AS DECIMAL(38, 2)) END
            AS debt_like_ledgers,
        CAST(coalesce(s.debt_like_items, 0) AS DECIMAL(38, 2)) AS debt_like_items,
        CAST(coalesce(s.proposed_items, 0) AS DECIMAL(38, 2)) AS proposed_items,
        coalesce(s.proposed_item_count, 0) AS proposed_item_count,
        c.unclassified_ledgers, c.proposed_classes
    FROM periods p
    LEFT JOIN tb ON tb.period = p.period
    LEFT JOIN sums s ON s.period = p.period
    LEFT JOIN coverage c ON c.period = p.period
)
SELECT
    engagement_id, period, basis, period_end, cash, debt, debt_like_ledgers, debt_like_items,
    debt_like_ledgers + debt_like_items AS debt_like,
    debt + debt_like_ledgers + debt_like_items - cash AS net_debt,
    proposed_items, proposed_item_count, unclassified_ledgers, proposed_classes, file_name AS trial_balance_file, n
FROM figures;
