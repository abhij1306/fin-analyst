"""The SQL of the temporary view `rebuilt_statements` (fin.rebuilt.view makes it per connection).

It reads the schema view `rebuilt_ledgers` and four temporary tables: `statement_accounts` and
`statement_paths` (the dictionary's account tree, from fin.statements), `rebuilt_accounts` (each
balance sheet and profit and loss account's side and the cash flow line its change goes to) and
`rebuilt_cash_lines` (each cash flow line's activity).
"""

VIEW_SQL = """
CREATE OR REPLACE TEMP VIEW rebuilt_statements AS
WITH years AS (
    SELECT DISTINCT period, period_start, period_end,
        CAST(datediff('month', period_start, period_end + 1) AS INTEGER) AS period_months
    FROM rebuilt_ledgers
),
-- Each ledger and the account it counts at: its confirmed account (mapped), else its proposed
-- one (target only: it marks the totals above it incomplete), else none.
placed AS (
    SELECT l.period, l.file_name, l.sheet, l."row", l.ledger, l.net, a.account AS target,
        a.statement, l.account IS NOT NULL AND a.account IS NOT NULL AS mapped
    FROM rebuilt_ledgers l
    LEFT JOIN statement_accounts a ON a.account = coalesce(l.account, l.proposed_account)
        AND a.statement IN ('balance_sheet', 'profit_and_loss')
),
-- The account and every subtotal above it; a profit and loss ledger also counts in other equity
-- and above (the year's profit, as the balance sheet shows it).
reach AS (
    SELECT p.*, p.target AS node FROM placed p WHERE p.target IS NOT NULL
    UNION ALL
    SELECT p.*, s.ancestor FROM placed p JOIN statement_paths s ON s.descendant = p.target
    UNION ALL
    SELECT p.*, r.node FROM placed p
    JOIN (
        SELECT '{retained}' AS node
        UNION ALL SELECT ancestor FROM statement_paths WHERE descendant = '{retained}'
    ) r ON true
    WHERE p.statement = 'profit_and_loss'
),
totals AS (
    SELECT period, node AS account,
        sum(net) FILTER (WHERE mapped) AS net,
        count(*) FILTER (WHERE mapped) AS ledgers,
        sum(net) FILTER (WHERE NOT mapped) AS unmapped,
        count(*) FILTER (WHERE NOT mapped) AS unmapped_ledgers,
        list(file_name || ' ' || sheet || '!' || CAST("row" AS VARCHAR)
            ORDER BY file_name, sheet, "row") FILTER (WHERE mapped) AS refs
    FROM reach
    GROUP BY period, node
),
-- Unmapped ledgers with no proposed account: under every total of their year.
loose AS (
    SELECT period, sum(net) AS net, count(*) AS n
    FROM placed WHERE NOT mapped AND target IS NULL GROUP BY period
),
unmapped_year AS (
    SELECT period, sum(net) AS net, count(*) AS n FROM placed WHERE NOT mapped GROUP BY period
),
bs_pl AS (
    SELECT t.period, a.statement, 2 * a.position AS line_no,
        CASE
            WHEN NOT a.running
                THEN (SELECT count(*) FROM statement_paths s WHERE s.descendant = a.account)
            WHEN a.is_root OR EXISTS (
                SELECT 1 FROM statement_paths s JOIN totals d ON d.account = s.descendant
                WHERE s.ancestor = a.account AND d.period = t.period AND d.ledgers > 0
            ) THEN 0
            ELSE 1
        END AS depth,
        'line' AS line_type, a.account, a.label, k.side * t.net AS value, t.ledgers,
        t.unmapped_ledgers + coalesce(o.n, 0) AS unmapped_ledgers,
        CASE WHEN t.unmapped_ledgers + coalesce(o.n, 0) > 0
            THEN coalesce(t.unmapped, 0) + coalesce(o.net, 0) END AS unmapped,
        array_to_string(list_slice(t.refs, 1, {shown}), ', ') || CASE WHEN len(t.refs) > {shown}
            THEN ' and ' || CAST(len(t.refs) - {shown} AS VARCHAR) || ' more' ELSE '' END
            AS sources,
        CAST(NULL AS VARCHAR) AS note
    FROM totals t
    JOIN statement_accounts a ON a.account = t.account
    JOIN rebuilt_accounts k ON k.account = t.account
    LEFT JOIN loose o ON o.period = t.period
    WHERE t.ledgers > 0
),
-- A year has a cash flow when the trial balance of the day before it starts is loaded.
pairs AS (
    SELECT c.period, p.period AS prior
    FROM years c JOIN years p ON p.period_end = CAST(c.period_start - INTERVAL 1 DAY AS DATE)
),
profit AS (
    SELECT period, max(value) FILTER (WHERE account = 'pat') AS pat,
        max(value) FILTER (WHERE account = 'depreciation') AS dep
    FROM bs_pl GROUP BY period
),
-- Balance sheet balances (debit - credit) by the account the ledger is confirmed to.
own AS (
    SELECT period,
        CASE WHEN statement = 'profit_and_loss' THEN '{retained}' ELSE target END AS account,
        sum(net) AS net
    FROM placed WHERE mapped GROUP BY ALL
),
-- The cash effect of each balance's change: what it was at the start less what it is at the end.
moves AS (
    SELECT q.period, k.cash_line AS line,
        sum(CASE WHEN w.period = q.period THEN -w.net ELSE w.net END) AS value
    FROM pairs q
    JOIN own w ON w.period IN (q.period, q.prior)
    JOIN rebuilt_accounts k ON k.account = w.account
    WHERE k.cash_line <> 'cash'
    GROUP BY ALL
),
cash AS (
    SELECT q.period,
        CASE WHEN count(w.account) > 0
            THEN coalesce(sum(w.net) FILTER (WHERE w.period = q.prior), 0) END AS opening,
        CASE WHEN count(w.account) > 0
            THEN coalesce(sum(w.net) FILTER (WHERE w.period = q.period), 0) END AS closing
    FROM pairs q
    LEFT JOIN own w ON w.period IN (q.period, q.prior)
        AND w.account IN (SELECT account FROM rebuilt_accounts WHERE cash_line = 'cash')
    GROUP BY q.period
),
-- Profit for the year starts operating activities; depreciation is added back there and taken
-- from the change in fixed assets (purchases net of disposals); the year's profit is taken from
-- the change in other equity, which leaves dividends and other equity movements.
cf_items AS (
    SELECT period, line, value FROM moves
    UNION ALL
    SELECT q.period, 'cf_pat', f.pat FROM pairs q JOIN profit f USING (period)
    WHERE f.pat IS NOT NULL
    UNION ALL
    SELECT q.period, 'cf_add_depreciation', f.dep FROM pairs q JOIN profit f USING (period)
    WHERE f.dep IS NOT NULL
    UNION ALL
    SELECT q.period, 'cf_capex', -f.dep FROM pairs q JOIN profit f USING (period)
    WHERE f.dep IS NOT NULL
    UNION ALL
    SELECT q.period, '{equity}', -f.pat FROM pairs q JOIN profit f USING (period)
    WHERE f.pat IS NOT NULL
),
cf_detail AS (
    SELECT i.period, i.line, c.activity, sum(i.value) AS value
    FROM cf_items i JOIN rebuilt_cash_lines c USING (line)
    GROUP BY ALL
),
cf_totals AS (
    SELECT q.period,
        coalesce(sum(d.value) FILTER (WHERE d.activity = 'operating'), 0) AS cfo,
        coalesce(sum(d.value) FILTER (WHERE d.activity = 'investing'), 0) AS cfi,
        coalesce(sum(d.value) FILTER (WHERE d.activity = 'financing'), 0) AS cff
    FROM pairs q LEFT JOIN cf_detail d USING (period)
    GROUP BY q.period
),
cf_values AS (
    SELECT period, line AS key, value, 'line' AS line_type, NULL AS note FROM cf_detail
    UNION ALL SELECT period, 'cf_cash_generated', cfo, 'line', NULL FROM cf_totals
    UNION ALL SELECT period, 'cfo', cfo, 'line', NULL FROM cf_totals
    UNION ALL SELECT period, 'cfi', cfi, 'line', NULL FROM cf_totals
    UNION ALL SELECT period, 'cff', cff, 'line', NULL FROM cf_totals
    UNION ALL SELECT period, 'net_change_cash', cfo + cfi + cff, 'line', NULL FROM cf_totals
    UNION ALL SELECT period, 'opening_cash', opening, 'line', NULL FROM cash
    UNION ALL SELECT period, 'closing_cash', closing, 'line', NULL FROM cash
    UNION ALL
    SELECT period, '{other}', closing - opening - (cfo + cfi + cff), 'other', NULL
    FROM cf_totals JOIN cash USING (period)
    WHERE closing - opening - (cfo + cfi + cff) <> 0
    UNION ALL SELECT period, 'cf_taxes_paid', NULL, 'not_derivable', '{taxes_note}' FROM pairs
    UNION ALL
    SELECT period, 'cf_finance_cost_paid', NULL, 'not_derivable', '{interest_note}' FROM pairs
),
cf_rows AS (
    SELECT v.period, 'cash_flow' AS statement,
        CASE v.key
            WHEN '{equity}'
                THEN 2 * (SELECT position FROM statement_accounts WHERE account = 'cff') - 1
            WHEN '{other}' THEN 2 * (
                SELECT position FROM statement_accounts WHERE account = 'closing_cash'
            ) - 1
            ELSE 2 * a.position
        END AS line_no,
        CASE WHEN v.key IN ('cf_cash_generated', 'cfo', 'cfi', 'cff', 'net_change_cash',
            'closing_cash') THEN 0 ELSE 1 END AS depth,
        v.line_type,
        CASE WHEN v.key IN ('{equity}', '{other}') THEN NULL ELSE v.key END AS account,
        CASE v.key WHEN '{equity}' THEN '{equity_label}' WHEN '{other}' THEN '{other_label}'
            ELSE a.label END AS label,
        CAST(v.value AS DECIMAL(38, 2)) AS value, CAST(NULL AS BIGINT) AS ledgers,
        coalesce(u.n, 0) + coalesce(w.n, 0) AS unmapped_ledgers,
        CAST(NULL AS DECIMAL(38, 2)) AS unmapped, CAST(NULL AS VARCHAR) AS sources,
        CAST(v.note AS VARCHAR) AS note
    FROM cf_values v
    JOIN pairs q USING (period)
    LEFT JOIN statement_accounts a ON a.account = v.key
    LEFT JOIN unmapped_year u ON u.period = q.period
    LEFT JOIN unmapped_year w ON w.period = q.prior
),
memo AS (
    SELECT u.period, s.statement, 1000000 AS line_no, 0 AS depth, 'unmapped' AS line_type,
        CAST(NULL AS VARCHAR) AS account, '{unmapped_label}' AS label,
        CAST(u.net AS DECIMAL(38, 2)) AS value, CAST(NULL AS BIGINT) AS ledgers,
        u.n AS unmapped_ledgers, CAST(u.net AS DECIMAL(38, 2)) AS unmapped,
        CAST(NULL AS VARCHAR) AS sources, CAST(NULL AS VARCHAR) AS note
    FROM unmapped_year u
    JOIN (VALUES ('balance_sheet'), ('profit_and_loss'), ('cash_flow')) s(statement) ON true
    WHERE s.statement <> 'cash_flow' OR u.period IN (SELECT period FROM pairs)
)
SELECT y.period, y.period_months, split_part(y.period, '-', 1) AS fiscal_year, y.period_end,
    'standalone' AS scope, x.statement, x.line_no, x.depth, x.line_type, x.account, x.label,
    x.value, x.ledgers, x.unmapped_ledgers, x.unmapped, x.unmapped_ledgers > 0 AS incomplete,
    x.sources, x.note
FROM (
    SELECT * FROM bs_pl UNION ALL BY NAME SELECT * FROM cf_rows
    UNION ALL BY NAME SELECT * FROM memo
) x
JOIN years y USING (period)
"""
