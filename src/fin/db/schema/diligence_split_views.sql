-- Company database: the pass-through views on the registers (docs/diligence.md D4). Run after sources.sql
-- (register_lines) and diligence.sql (passthrough_customers).
--
-- Which lines: the sales and purchase register lines of the latest engagement's loaded (not superseded) files, by
-- register_lines.period (the engagement's annual periods and its stub; a line dated outside them has no period and is
-- left out). Amounts are Amount in Rs, before GST, as filed. A customer is pass-through when its token is in the
-- latest engagement's passthrough_customers. The registers have no scope: these views are company-level.

-- Revenue per period of the latest engagement, split into core and pass-through (project revenue):
--   sales_revenue = core_revenue + passthrough_revenue, from the sales register (NULL when it has no line in the period);
--   passthrough_licence_revenue: pass-through lines with a Purchase Id (software or licence, an OEM purchase behind it);
--   passthrough_services_revenue: pass-through lines without one (services);
--   linked_purchase_cost: the purchase register lines whose Purchase Id is on a pass-through line of the period,
--   each Purchase Id counted once, whatever the purchase's own date (information, not a verdict: passthrough_lines
--   shows each link);
--   core_revenue_growth: core revenue against the previous annual period's, NULL for the stub, for the first period
--   in scope, and unless the earlier core revenue is positive. Pass-through revenue never enters growth.
-- A period without register lines still has its row, with NULLs.
CREATE VIEW revenue_split AS
WITH latest AS (
    SELECT engagement_id, periods, stub FROM engagement ORDER BY engagement_id DESC LIMIT 1
),
periods AS (
    SELECT engagement_id, unnest(string_split(periods, ',')) AS period, 'audited' AS basis FROM latest
    UNION ALL
    SELECT engagement_id, stub, 'unaudited' FROM latest WHERE stub IS NOT NULL
),
lines AS (
    SELECT
        r.kind, r.period, r.purchase_id, r.amount_inr,
        r.kind = 'sales' AND r.customer IN (
            SELECT p.customer FROM passthrough_customers p WHERE p.engagement_id = l.engagement_id
        ) AS passthrough
    FROM register_lines r
    JOIN source_files f ON f.file_id = r.file_id AND f.status = 'loaded'
    JOIN latest l ON l.engagement_id = f.engagement_id
    WHERE r.period IS NOT NULL OR r.kind = 'purchase'
),
sales AS (
    SELECT
        period,
        sum(amount_inr) AS sales_revenue,
        coalesce(sum(amount_inr) FILTER (WHERE NOT passthrough), 0) AS core_revenue,
        coalesce(sum(amount_inr) FILTER (WHERE passthrough), 0) AS passthrough_revenue,
        count(*) FILTER (WHERE passthrough) AS passthrough_lines,
        coalesce(sum(amount_inr) FILTER (WHERE passthrough AND purchase_id IS NOT NULL), 0)
            AS passthrough_licence_revenue,
        coalesce(sum(amount_inr) FILTER (WHERE passthrough AND purchase_id IS NULL), 0)
            AS passthrough_services_revenue
    FROM lines
    WHERE kind = 'sales' AND period IS NOT NULL
    GROUP BY period
),
linked AS (
    SELECT s.period, sum(p.amount_inr) AS linked_purchase_cost
    FROM (SELECT DISTINCT period, purchase_id FROM lines WHERE passthrough AND purchase_id IS NOT NULL AND period IS NOT NULL) s
    JOIN lines p ON p.kind = 'purchase' AND p.purchase_id = s.purchase_id
    GROUP BY s.period
),
figures AS (
    SELECT
        p.engagement_id, p.period, p.basis,
        CAST(s.sales_revenue AS DECIMAL(38, 2)) AS sales_revenue,
        CAST(s.core_revenue AS DECIMAL(38, 2)) AS core_revenue,
        CAST(s.passthrough_revenue AS DECIMAL(38, 2)) AS passthrough_revenue,
        s.passthrough_lines,
        CAST(s.passthrough_licence_revenue AS DECIMAL(38, 2)) AS passthrough_licence_revenue,
        CAST(s.passthrough_services_revenue AS DECIMAL(38, 2)) AS passthrough_services_revenue,
        CAST(CASE WHEN s.period IS NOT NULL THEN coalesce(k.linked_purchase_cost, 0) END AS DECIMAL(38, 2))
            AS linked_purchase_cost
    FROM periods p
    LEFT JOIN sales s ON s.period = p.period
    LEFT JOIN linked k ON k.period = p.period
)
SELECT
    f.engagement_id, f.period, f.basis, f.sales_revenue, f.core_revenue, f.passthrough_revenue,
    div6(to_paise(f.passthrough_revenue), to_paise(f.sales_revenue)) AS passthrough_share,
    f.passthrough_lines, f.passthrough_licence_revenue, f.passthrough_services_revenue, f.linked_purchase_cost,
    CASE WHEN f.basis = 'audited' THEN growth6(to_paise(f.core_revenue), to_paise(e.core_revenue)) END
        AS core_revenue_growth
FROM figures f
LEFT JOIN figures e ON e.basis = 'audited' AND e.period = CASE WHEN f.basis = 'audited' THEN prior_fy(f.period) END;

-- Each pass-through sales line of the latest engagement with the OEM purchase it points to (its Purchase Id):
-- link 'licence' (Purchase Id given: software or licence) or 'services' (none). The purchase columns are NULL when
-- the Purchase Id is on no purchase line, and a Purchase Id on several purchase lines gives a row per purchase line:
-- do not add amount_inr across rows of one sale, use revenue_split for totals. Names are tokens.
CREATE VIEW passthrough_lines AS
WITH latest AS (
    SELECT engagement_id FROM engagement ORDER BY engagement_id DESC LIMIT 1
),
loaded AS (
    SELECT r.*, f.file_name
    FROM register_lines r
    JOIN source_files f ON f.file_id = r.file_id AND f.status = 'loaded'
    JOIN latest l ON l.engagement_id = f.engagement_id
)
SELECT
    l.engagement_id, s.period, s.customer, s.invoice_no, s.bill_id, s.purchase_id,
    CASE WHEN s.purchase_id IS NOT NULL THEN 'licence' ELSE 'services' END AS link,
    s.amount_inr,
    s.file_name AS sales_file, s.sheet AS sales_sheet, s."row" AS sales_row,
    p.vendor, p.invoice_no AS purchase_invoice_no, p.amount_inr AS purchase_amount_inr,
    p.file_name AS purchase_file, p.sheet AS purchase_sheet, p."row" AS purchase_row
FROM loaded s
JOIN latest l ON true
LEFT JOIN loaded p ON p.kind = 'purchase' AND p.purchase_id = s.purchase_id
WHERE s.kind = 'sales'
  AND s.customer IN (SELECT c.customer FROM passthrough_customers c WHERE c.engagement_id = l.engagement_id)
ORDER BY s.period, s.invoice_no, p."row";
