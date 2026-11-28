-- Company database: the ledgers the rebuilt statements are made from (SPEC-14, fin.rebuilt). Run after sources.sql
-- (engagement, source_files, trial_balance, ledger_accounts). Money is DECIMAL(20,2) rupees.

-- One row per ledger row of each loaded (current) trial balance of the latest engagement: its closing balance as
-- debit - credit (net), where it is in the file (file_name, sheet, row), and its account: account when the user
-- confirmed it, proposed_account while it waits for review (status 'proposed'). A ledger with no confirmed account is
-- never rolled up: the view `rebuilt_statements` counts it as unmapped. Group rows are left out: a trial balance with
-- group totals only gives no row here.
CREATE VIEW rebuilt_ledgers AS
SELECT
    s.engagement_id,
    s.period,
    s.period_start,
    s.period_end,
    s.basis,
    s.file_id,
    s.file_name,
    t.sheet,
    t."row",
    t.ledger,
    t.closing_debit - t.closing_credit AS net,
    CASE WHEN m.status = 'confirmed' THEN m.account END AS account,
    CASE WHEN m.status = 'proposed' THEN m.account END AS proposed_account,
    coalesce(m.status, 'pending') AS mapping_status
FROM source_files s
JOIN trial_balance t ON t.file_id = s.file_id AND t.row_type = 'ledger'
LEFT JOIN ledger_accounts m ON m.ledger = t.ledger
WHERE s.kind = 'trial_balance' AND s.status = 'loaded'
    AND s.engagement_id = (SELECT max(engagement_id) FROM engagement);
