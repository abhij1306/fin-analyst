-- Views over the source-file tables of sources.sql. Runs right after sources.sql.

-- Collections per customer and period, from the latest bank_vs_invoices run of the latest engagement (the invoices in
-- its lines; customer is the token). invoiced_inr: invoices of the period's lines; received_inr: those matched to a
-- receipt in full (matched lines); partly_paid_inr: invoices in a group whose receipts differ from them (kind
-- 'difference': the invoice amount, not what was received); open_inr: those with no receipt.
-- invoiced_inr = received_inr + partly_paid_inr + open_inr. collection_days: the average days from
-- invoice to receipt over the matched invoices, weighted by Amount in Rs (a group of several lines uses its latest
-- dates); NULL with none. Open invoices are aged at as_of (the period's last day): not_due is up to due_days old,
-- overdue_* the days past due_days. Invoices of a period with no bank statement are not in a run.
CREATE VIEW customer_collections AS
WITH latest AS (
    SELECT recon_id, period, as_of, due_days FROM recon_runs
    WHERE name = 'bank_vs_invoices' AND engagement_id = (SELECT max(engagement_id) FROM engagement)
    QUALIFY row_number() OVER (PARTITION BY period ORDER BY recon_id DESC) = 1
),
spans AS (
    SELECT recon_id, item_no,
        max(line_date) FILTER (WHERE side = 'bank') - max(line_date) FILTER (WHERE side = 'invoice') AS days
    FROM recon_match_lines GROUP BY recon_id, item_no
),
inv AS (
    SELECT l.period, l.as_of, l.due_days, m.party AS customer, i.kind, m.amount_inr, m.line_date, s.days
    FROM recon_match_lines m
    JOIN latest l USING (recon_id)
    JOIN recon_items i ON i.recon_id = m.recon_id AND i.item_no = m.item_no
    JOIN spans s ON s.recon_id = m.recon_id AND s.item_no = m.item_no
    WHERE m.side = 'invoice' AND i.kind <> 'fx'
)
SELECT
    period,
    customer,
    sum(amount_inr) AS invoiced_inr,
    coalesce(sum(amount_inr) FILTER (WHERE kind = 'matched'), 0) AS received_inr,
    coalesce(sum(amount_inr) FILTER (WHERE kind = 'right_only'), 0) AS open_inr,
    round(sum(amount_inr * days) FILTER (WHERE kind = 'matched')
        / nullif(sum(amount_inr) FILTER (WHERE kind = 'matched'), 0), 1) AS collection_days,
    coalesce(sum(amount_inr) FILTER (WHERE kind = 'difference'), 0) AS partly_paid_inr,
    coalesce(sum(amount_inr) FILTER (
        WHERE kind = 'right_only' AND date_diff('day', line_date, as_of) <= due_days), 0) AS not_due_inr,
    coalesce(sum(amount_inr) FILTER (
        WHERE kind = 'right_only' AND date_diff('day', line_date, as_of) - due_days BETWEEN 1 AND 30), 0)
        AS overdue_1_30_inr,
    coalesce(sum(amount_inr) FILTER (
        WHERE kind = 'right_only' AND date_diff('day', line_date, as_of) - due_days BETWEEN 31 AND 60), 0)
        AS overdue_31_60_inr,
    coalesce(sum(amount_inr) FILTER (
        WHERE kind = 'right_only' AND date_diff('day', line_date, as_of) - due_days BETWEEN 61 AND 90), 0)
        AS overdue_61_90_inr,
    coalesce(sum(amount_inr) FILTER (
        WHERE kind = 'right_only' AND date_diff('day', line_date, as_of) - due_days > 90), 0)
        AS overdue_over_90_inr
FROM inv
GROUP BY period, customer;
