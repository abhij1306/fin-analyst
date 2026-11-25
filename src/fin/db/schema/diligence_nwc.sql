-- Company database: normalised working capital (docs/diligence.md D7). Run after sources.sql (trial_balance,
-- ledger_entries, ledger_accounts, recon_runs) and diligence.sql (engagement).
--
-- Which ledgers: those the user confirmed (ledger_accounts, status 'confirmed') to a working capital account, listed
-- in nwc_accounts below. Cash, bank balances, investments, borrowings, lease liabilities and tax assets and
-- liabilities are not in it (they are net debt or tax, not trade working capital).
-- Which files: the loaded (not superseded) trial balances and Ledger Vouchers of the latest engagement. Balances are
-- shown as the statements show them (assets and liabilities both positive): (closing debit - closing credit) * side.
-- Net working capital = assets - liabilities. Money is exact DECIMAL(38, 2) rupees. A ledger not confirmed to an
-- account is counted in unmapped_ledgers, never guessed; the views are empty when the company has no engagement.

-- The working capital accounts: component is the line the account is shown in; nwc_sign is 1 for an asset (adds to
-- net working capital) and -1 for a liability (subtracts).
CREATE VIEW nwc_accounts AS
SELECT * FROM (VALUES
    ('trade_receivables', 'trade_receivables', 1),
    ('inventories', 'inventories', 1),
    ('other_current_assets', 'other_current_assets', 1),
    ('loans_current', 'other_current_assets', 1),
    ('other_financial_assets_current', 'other_current_assets', 1),
    ('trade_payables', 'trade_payables', -1),
    ('other_current_liabilities', 'other_current_liabilities', -1),
    ('other_financial_liabilities_current', 'other_current_liabilities', -1),
    ('st_provisions', 'other_current_liabilities', -1)
) AS t(account, component, nwc_sign);

-- Each working capital ledger's closing balance in each loaded trial balance (the annual periods and the stub), with
-- its row in the file (what nwc_year_end adds up).
CREATE VIEW nwc_year_end_lines AS
SELECT
    s.engagement_id, s.period, s.basis, s.period_end, n.component, n.account, t.ledger, n.nwc_sign,
    CAST((t.closing_debit - t.closing_credit) * m.side AS DECIMAL(38, 2)) AS balance,
    s.file_name, t.sheet, t."row", s.file_id
FROM source_files s
JOIN trial_balance t ON t.file_id = s.file_id AND t.row_type = 'ledger'
JOIN ledger_accounts m ON m.ledger = t.ledger AND m.status = 'confirmed'
JOIN nwc_accounts n ON n.account = m.account
WHERE s.kind = 'trial_balance' AND s.status = 'loaded'
    AND s.engagement_id = (SELECT max(engagement_id) FROM engagement);

-- Working capital at each year-end and at the stub's end, one row per loaded trial balance of the latest engagement.
-- basis is 'audited' for an annual period and 'unaudited' for the stub (unaudited management accounts). The five
-- components are the balances as shown; net_working_capital = receivables + inventories + other current assets -
-- payables - other current liabilities. mapped_ledgers / unmapped_ledgers: the trial balance's ledgers confirmed and
-- not confirmed to an account (while any is unmapped, the figures may be incomplete).
CREATE VIEW nwc_year_end AS
WITH tbs AS (
    SELECT s.engagement_id, s.period, s.basis, s.period_start, s.period_end, s.file_id, s.file_name
    FROM source_files s
    WHERE s.kind = 'trial_balance' AND s.status = 'loaded'
        AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
),
parts AS (
    SELECT file_id, component, sum(balance) AS balance, sum(balance * nwc_sign) AS signed, count(*) AS ledgers
    FROM nwc_year_end_lines GROUP BY file_id, component
),
coverage AS (
    SELECT t.file_id,
        count(*) FILTER (WHERE m.ledger IS NOT NULL) AS mapped_ledgers,
        count(*) FILTER (WHERE m.ledger IS NULL) AS unmapped_ledgers
    FROM trial_balance t
    LEFT JOIN ledger_accounts m ON m.ledger = t.ledger AND m.status = 'confirmed'
    WHERE t.row_type = 'ledger'
    GROUP BY t.file_id
)
SELECT
    b.engagement_id, b.period, b.basis, b.period_start, b.period_end,
    CAST(coalesce(sum(p.balance) FILTER (WHERE p.component = 'trade_receivables'), 0) AS DECIMAL(38, 2))
        AS trade_receivables,
    CAST(coalesce(sum(p.balance) FILTER (WHERE p.component = 'inventories'), 0) AS DECIMAL(38, 2)) AS inventories,
    CAST(coalesce(sum(p.balance) FILTER (WHERE p.component = 'other_current_assets'), 0) AS DECIMAL(38, 2))
        AS other_current_assets,
    CAST(coalesce(sum(p.balance) FILTER (WHERE p.component = 'trade_payables'), 0) AS DECIMAL(38, 2))
        AS trade_payables,
    CAST(coalesce(sum(p.balance) FILTER (WHERE p.component = 'other_current_liabilities'), 0) AS DECIMAL(38, 2))
        AS other_current_liabilities,
    CAST(coalesce(sum(p.signed), 0) AS DECIMAL(38, 2)) AS net_working_capital,
    coalesce(max(c.mapped_ledgers), 0) AS mapped_ledgers,
    coalesce(max(c.unmapped_ledgers), 0) AS unmapped_ledgers,
    b.file_name AS trial_balance_file
FROM tbs b
LEFT JOIN parts p ON p.file_id = b.file_id
LEFT JOIN coverage c ON c.file_id = b.file_id
GROUP BY b.engagement_id, b.period, b.basis, b.period_start, b.period_end, b.file_name;

-- The working capital ledgers of each period's Ledger Vouchers and whether the monthly view can use them. The
-- period's opening is the previous period's trial balance closing (the previous annual period; the last annual
-- period for the stub); a ledger absent from that trial balance opens at nil, as in gl_vs_tb. A ledger is
--   included  when the previous trial balance is loaded and the latest gl_vs_tb run of the period has its movement
--             equal to the trial balance (a 'matched' ledger line);
--   excluded  otherwise, with the reason: no previous trial balance (an opening is never inferred from the same
--             movements), gl_vs_tb has not run on the current files (the latest run's Ledger Vouchers and trial balance
--             are the loaded ones and it ran after the previous trial balance was loaded), or its movement does not tie
--             to the trial balance.
-- opening is the previous closing in the statements' sign (NULL: the ledger is not in it) and opening_source its
-- file + sheet!row.
CREATE VIEW nwc_monthly_ledgers AS
WITH eng AS (
    SELECT engagement_id, string_split(periods, ',') AS annual, stub
    FROM engagement ORDER BY engagement_id DESC LIMIT 1
),
lv AS (
    SELECT s.period, s.period_start, s.period_end, s.file_id, s.file_name, e.engagement_id,
        CASE
            WHEN s.period = e.stub THEN e.annual[-1]
            WHEN list_position(e.annual, s.period) > 1 THEN e.annual[list_position(e.annual, s.period) - 1]
        END AS prior_period
    FROM source_files s JOIN eng e ON e.engagement_id = s.engagement_id
    WHERE s.kind = 'ledger_vouchers' AND s.status = 'loaded'
),
tb AS (
    SELECT s.period, s.file_id, s.file_name, s.ingested_at FROM source_files s
    WHERE s.kind = 'trial_balance' AND s.status = 'loaded'
        AND s.engagement_id = (SELECT engagement_id FROM eng)
),
ledgers AS (
    SELECT lv.period, b.ledger FROM lv JOIN ledger_blocks b ON b.file_id = lv.file_id
    UNION
    SELECT lv.period, t.ledger FROM lv JOIN tb ON tb.period = lv.period
    JOIN trial_balance t ON t.file_id = tb.file_id AND t.row_type = 'ledger'
),
runs AS (
    SELECT recon_id, period FROM recon_runs
    WHERE name = 'gl_vs_tb' AND engagement_id = (SELECT engagement_id FROM eng)
    QUALIFY row_number() OVER (PARTITION BY period ORDER BY recon_id DESC) = 1
),
opening AS (
    SELECT tb.period, t.ledger, sum(t.closing_debit - t.closing_credit) AS net,
        min(tb.file_name || ' ' || t.sheet || '!' || t."row") AS source
    FROM tb JOIN trial_balance t ON t.file_id = tb.file_id AND t.row_type = 'ledger'
    GROUP BY tb.period, t.ledger
)
SELECT
    lv.engagement_id, lv.period, l.ledger, n.account, n.component, n.nwc_sign, m.side,
    lv.file_id AS ledger_vouchers_file_id, lv.file_name AS ledger_vouchers_file, lv.prior_period,
    CASE
        WHEN ptb.period IS NULL THEN 'excluded'
        WHEN r.recon_id IS NULL OR i.kind IS NULL THEN 'excluded'
        WHEN i.kind = 'matched' THEN 'included'
        ELSE 'excluded'
    END AS status,
    CASE
        WHEN ptb.period IS NULL THEN 'no previous trial balance'
        WHEN r.recon_id IS NULL THEN 'gl_vs_tb has not run on the current files'
        WHEN i.kind IS NULL THEN 'gl_vs_tb has no line for it'
        WHEN i.kind <> 'matched' THEN 'movements do not tie to the trial balance'
    END AS reason,
    CAST(o.net * m.side AS DECIMAL(38, 2)) AS opening,
    o.source AS opening_source
FROM lv
JOIN ledgers l ON l.period = lv.period
JOIN ledger_accounts m ON m.ledger = l.ledger AND m.status = 'confirmed'
JOIN nwc_accounts n ON n.account = m.account
LEFT JOIN tb ptb ON ptb.period = lv.prior_period
LEFT JOIN tb ctb ON ctb.period = lv.period
LEFT JOIN opening o ON o.period = lv.prior_period AND o.ledger = l.ledger
LEFT JOIN recon_runs r ON r.recon_id = (SELECT recon_id FROM runs WHERE runs.period = lv.period)
    AND r.left_file_id = lv.file_id AND r.right_file_id = ctb.file_id AND r.created_at >= ptb.ingested_at
LEFT JOIN recon_items i ON i.recon_id = r.recon_id AND i.level = 'ledger' AND i.key = l.ledger;

-- The balance of each included ledger at each month end of its period (the Ledger Vouchers' span: a month per row,
-- the stub's last month ending on its end date): opening + the cumulative movement (debit - credit of its entries
-- dated up to the month end), in the statements' sign. entries: how many voucher lines are in it.
CREATE VIEW nwc_monthly_lines AS
WITH months AS (
    SELECT DISTINCT s.period,
        least(CAST(last_day(m.month_start) AS DATE), s.period_end) AS month_end
    FROM source_files s,
        LATERAL (SELECT unnest(generate_series(
            date_trunc('month', s.period_start), date_trunc('month', s.period_end), INTERVAL 1 MONTH)) AS month_start) m
    WHERE s.kind = 'ledger_vouchers' AND s.status = 'loaded'
        AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
)
SELECT
    i.engagement_id, i.period, mo.month_end, i.component, i.account, i.ledger, i.nwc_sign,
    CAST((coalesce(i.opening, 0) * i.side + coalesce(e.movement, 0)) * i.side AS DECIMAL(38, 2)) AS balance,
    i.opening, i.opening_source, coalesce(e.entries, 0) AS entries, i.ledger_vouchers_file
FROM nwc_monthly_ledgers i
JOIN months mo ON mo.period = i.period
LEFT JOIN LATERAL (
    SELECT sum(x.debit - x.credit) AS movement, count(*) AS entries
    FROM ledger_entries x
    WHERE x.file_id = i.ledger_vouchers_file_id AND x.ledger = i.ledger AND x.entry_date <= mo.month_end
) e ON true
WHERE i.status = 'included';

-- Working capital at each month end, from Ledger Vouchers movements on the previous trial balance's closing: one
-- row per month of each period that has at least one included ledger. Columns as nwc_year_end. ledgers_included /
-- ledgers_excluded: the period's working capital ledgers in and out of the view (nwc_monthly_ledgers says why): a
-- month with excluded ledgers is not the complete working capital. There are no monthly trial balances, so this is
-- the only monthly view.
CREATE VIEW nwc_monthly AS
WITH counts AS (
    SELECT period, count(*) FILTER (WHERE status = 'included') AS ledgers_included,
        count(*) FILTER (WHERE status = 'excluded') AS ledgers_excluded
    FROM nwc_monthly_ledgers GROUP BY period
)
SELECT
    l.engagement_id, l.period, l.month_end,
    CAST(coalesce(sum(l.balance) FILTER (WHERE l.component = 'trade_receivables'), 0) AS DECIMAL(38, 2))
        AS trade_receivables,
    CAST(coalesce(sum(l.balance) FILTER (WHERE l.component = 'inventories'), 0) AS DECIMAL(38, 2)) AS inventories,
    CAST(coalesce(sum(l.balance) FILTER (WHERE l.component = 'other_current_assets'), 0) AS DECIMAL(38, 2))
        AS other_current_assets,
    CAST(coalesce(sum(l.balance) FILTER (WHERE l.component = 'trade_payables'), 0) AS DECIMAL(38, 2))
        AS trade_payables,
    CAST(coalesce(sum(l.balance) FILTER (WHERE l.component = 'other_current_liabilities'), 0) AS DECIMAL(38, 2))
        AS other_current_liabilities,
    CAST(sum(l.balance * l.nwc_sign) AS DECIMAL(38, 2)) AS net_working_capital,
    c.ledgers_included, c.ledgers_excluded
FROM nwc_monthly_lines l
JOIN counts c ON c.period = l.period
GROUP BY l.engagement_id, l.period, l.month_end, c.ledgers_included, c.ledgers_excluded;

-- Year-end (and stub-end) and month-end working capital in one list, for the dashboard: granularity 'year_end' (as_of
-- the period's end date, from the trial balance) or 'month_end' (from the Ledger Vouchers). ledgers_included and
-- ledgers_excluded are NULL for a year-end row; unmapped_ledgers is NULL for a month-end row.
CREATE VIEW nwc_series AS
SELECT
    period, 'year_end' AS granularity, period_end AS as_of, basis, trade_receivables, inventories,
    other_current_assets, trade_payables, other_current_liabilities, net_working_capital,
    unmapped_ledgers, NULL::BIGINT AS ledgers_included, NULL::BIGINT AS ledgers_excluded
FROM nwc_year_end
UNION ALL
SELECT
    m.period, 'month_end', m.month_end, CASE WHEN m.period = e.stub THEN 'unaudited' ELSE 'audited' END,
    m.trade_receivables, m.inventories, m.other_current_assets, m.trade_payables, m.other_current_liabilities,
    m.net_working_capital, NULL::BIGINT, m.ledgers_included, m.ledgers_excluded
FROM nwc_monthly m
JOIN (SELECT stub FROM engagement ORDER BY engagement_id DESC LIMIT 1) e ON true;
