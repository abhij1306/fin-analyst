-- Company database: FDD F8, the FDD findings (docs/fdd.md F8, docs/decisions/fdd-f8.md). Run after the diligence and
-- FDD files (it reads engagement, adjustments, netdebt_items, recon_runs, recon_items and accountant_queries).

-- A finding of the FDD report: Claude proposes it (`fin fdd finding propose`) and edits it while it is a draft; only
-- the user marks it final (`fin fdd finding final`) or returns it to draft (`--undo`). Dropped drafts stay, as
-- 'dropped', for the audit trail. The amount is never typed: it comes from the linked evidence (view
-- fdd_finding_rows), at most one of adj_id, netdebt_item_id, recon_id, else the linked query's amount. query_id also
-- gives the management response (the query's answer). Text has no typed number.
--   area:               the report section it belongs to;
--   impact_type:        ebitda, net_debt, working_capital, revenue, disclosure or none;
--   deal_consideration: price, spa_protection (SPA indemnity or warranty), condition_precedent, further_work, none.
CREATE SEQUENCE fdd_findings_seq START 1;
CREATE TABLE fdd_findings (
    finding_id INTEGER PRIMARY KEY DEFAULT nextval('fdd_findings_seq'),
    engagement_id INTEGER NOT NULL,
    area VARCHAR NOT NULL CHECK (area IN ('qoe', 'working_capital', 'net_debt', 'revenue', 'walkthroughs',
        'provisions', 'tax', 'contingencies', 'related_parties', 'inquiries', 'other')),
    title VARCHAR NOT NULL,
    observation VARCHAR NOT NULL,
    implication VARCHAR,
    impact_type VARCHAR NOT NULL
        CHECK (impact_type IN ('ebitda', 'net_debt', 'working_capital', 'revenue', 'disclosure', 'none')),
    severity VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    deal_consideration VARCHAR NOT NULL CHECK (deal_consideration IN
        ('price', 'spa_protection', 'condition_precedent', 'further_work', 'none')),
    evidence VARCHAR NOT NULL,
    adj_id INTEGER,
    netdebt_item_id INTEGER,
    recon_id INTEGER,
    query_id INTEGER,
    status VARCHAR NOT NULL CHECK (status IN ('draft', 'final', 'dropped')),
    created_run_id VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    status_run_id VARCHAR NOT NULL,
    status_at TIMESTAMP NOT NULL,
    -- What the user signed off: copied from fdd_finding_rows when the finding is made final, cleared when it returns
    -- to draft (docs/decisions/fdd-review-fixes.md).
    final_amount DECIMAL(38, 2),
    final_amount_source VARCHAR,
    final_link_status VARCHAR,
    final_response VARCHAR,
    final_response_source VARCHAR,
    CHECK ((adj_id IS NOT NULL)::INTEGER + (netdebt_item_id IS NOT NULL)::INTEGER
        + (recon_id IS NOT NULL)::INTEGER <= 1)
);

-- Each finding with its amount and where the amount comes from: an adjustment's amount, a net debt item's amount, a
-- reconciliation run's lines at or above the threshold (sum of the absolute differences in rupees: amount_inr, or the
-- difference of a rupee line; a foreign-currency line with no rupee value is left out), else the linked query's
-- amount; NULL when nothing is linked. The management response is the linked query's answer. link_status is the status
-- of the amount link (adjustment, net debt item), else of the query.
-- A final finding shows what the user signed off (the final_* snapshot) as amount, amount_source,
-- management_response and response_source; live_* are the values now. changed_since_final: a final finding whose live
-- amount, source, link status or response differs from the snapshot. link_flag: the linked adjustment or net debt
-- item is rejected, or the linked query is withdrawn (any status of the finding).
CREATE VIEW fdd_finding_rows AS
WITH live AS (
    SELECT f.*,
        CASE
            WHEN f.adj_id IS NOT NULL THEN a.amount
            WHEN f.netdebt_item_id IS NOT NULL THEN n.amount
            WHEN f.recon_id IS NOT NULL THEN (
                SELECT coalesce(sum(abs(coalesce(i.amount_inr,
                    CASE WHEN coalesce(i.currency, 'INR') = 'INR' THEN i.difference END))), 0)
                FROM recon_items i WHERE i.recon_id = f.recon_id AND i.needs_query)
            ELSE q.amount
        END AS live_amount,
        CASE
            WHEN f.adj_id IS NOT NULL THEN 'adjustment #' || f.adj_id || ' (' || a.status || ')'
            WHEN f.netdebt_item_id IS NOT NULL
                THEN 'net debt item #' || f.netdebt_item_id || ' (' || n.status || ')'
            WHEN f.recon_id IS NOT NULL
                THEN 'reconciliation ' || r.name || ' ' || r.period || ' run #' || f.recon_id
                    || ' (lines at or above the threshold)'
            WHEN q.amount IS NOT NULL THEN 'query #' || f.query_id
        END AS live_amount_source,
        coalesce(a.status, n.status, q.status) AS link_status,
        q.status AS query_status,
        q.answer AS live_response,
        q.answer_source AS live_response_source,
        CASE
            WHEN a.status = 'rejected' THEN 'linked adjustment #' || f.adj_id || ' is rejected'
            WHEN n.status = 'rejected' THEN 'linked net debt item #' || f.netdebt_item_id || ' is rejected'
            WHEN q.status = 'withdrawn' THEN 'linked query #' || f.query_id || ' is withdrawn'
        END AS link_flag
    FROM fdd_findings f
    LEFT JOIN adjustments a ON a.adj_id = f.adj_id
    LEFT JOIN netdebt_items n ON n.item_id = f.netdebt_item_id
    LEFT JOIN recon_runs r ON r.recon_id = f.recon_id
    LEFT JOIN accountant_queries q ON q.query_id = f.query_id
)
SELECT *,
    CASE WHEN status = 'final' THEN final_amount ELSE live_amount END AS amount,
    CASE WHEN status = 'final' THEN final_amount_source ELSE live_amount_source END AS amount_source,
    CASE WHEN status = 'final' THEN final_response ELSE live_response END AS management_response,
    CASE WHEN status = 'final' THEN final_response_source ELSE live_response_source END AS response_source,
    status = 'final' AND (live_amount IS DISTINCT FROM final_amount
        OR live_amount_source IS DISTINCT FROM final_amount_source
        OR link_status IS DISTINCT FROM final_link_status
        OR live_response IS DISTINCT FROM final_response) AS changed_since_final
FROM live;
