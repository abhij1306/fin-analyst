-- Company database: the diligence views (docs/DILIGENCE_PLAN.md, fin.diligence.bridge). Run after diligence.sql.
-- Plain views over the metrics and the adjustments, so they are always current and never stored twice.
--
-- Which figures:
-- - The latest engagement (the highest engagement_id, open or closed) and its annual periods; every view is empty
--   when the company has no engagement. Each row is a period of the engagement and a scope: every scope the
--   company has figures or adjustments for in those periods. A period without figures still has its row, with
--   NULLs, so nothing is hidden.
-- - Reported EBITDA is metrics_wide.ebitda: exactly the `ebitda` metric (company_views.sql, conventions).
-- - Adjustments: only accepted ones change EBITDA. Proposed ones are summed beside it for visibility, never applied;
--   rejected ones are counted only.
--
-- Rules (as metrics_wide):
-- - Money is exact DECIMAL(38, 2) rupees; ratios are div6 fractions (0.426761 means 42.6761%).
-- - A figure whose input is missing is NULL, never zero. A subtotal the document does not print is not replaced
--   by its lines here: stand_ins covers no cash flow subtotal, so a walk line that needs one is NULL.

-- Rupees -> integer paise, for div6.
CREATE MACRO to_paise(r) AS CAST(r * 100 AS HUGEINT);

-- The latest engagement's periods and scopes: one row per annual period in scope and scope.
CREATE VIEW engagement_periods AS
WITH latest AS (
    SELECT engagement_id, periods, status FROM engagement ORDER BY engagement_id DESC LIMIT 1
),
periods AS (
    SELECT engagement_id, status, unnest(string_split(periods, ',')) AS period FROM latest
),
scopes AS (
    SELECT m.scope FROM metrics_wide m JOIN periods p ON p.period = m.period
    UNION
    SELECT a.scope FROM adjustments a JOIN latest l ON l.engagement_id = a.engagement_id
)
SELECT p.engagement_id, p.status AS engagement_status, p.period, s.scope
FROM periods p, scopes s;

-- Adjusted EBITDA, one row per period and scope of the latest engagement:
--   adjusted_ebitda = reported_ebitda + accepted_adjustments.
-- accepted_adjustments and proposed_adjustments are 0 when there are none (a true zero: no adjustment); the
-- counts are per status. Margins are on revenue; NULL when revenue or EBITDA is missing.
CREATE VIEW adjusted_ebitda AS
WITH adj AS (
    SELECT
        a.period,
        a.scope,
        sum(a.amount) FILTER (WHERE a.status = 'accepted') AS accepted,
        sum(a.amount) FILTER (WHERE a.status = 'proposed') AS proposed,
        count(*) FILTER (WHERE a.status = 'accepted') AS accepted_count,
        count(*) FILTER (WHERE a.status = 'proposed') AS proposed_count,
        count(*) FILTER (WHERE a.status = 'rejected') AS rejected_count
    FROM adjustments a
    JOIN (SELECT DISTINCT engagement_id FROM engagement_periods) e ON e.engagement_id = a.engagement_id
    GROUP BY a.period, a.scope
),
figures AS (
    SELECT
        e.engagement_id,
        e.engagement_status,
        e.period,
        e.scope,
        CAST(m.revenue AS DECIMAL(38, 2)) AS revenue,
        CAST(m.ebitda AS DECIMAL(38, 2)) AS reported_ebitda,
        CAST(coalesce(adj.accepted, 0) AS DECIMAL(38, 2)) AS accepted_adjustments,
        CAST(coalesce(adj.proposed, 0) AS DECIMAL(38, 2)) AS proposed_adjustments,
        coalesce(adj.accepted_count, 0) AS accepted_count,
        coalesce(adj.proposed_count, 0) AS proposed_count,
        coalesce(adj.rejected_count, 0) AS rejected_count
    FROM engagement_periods e
    LEFT JOIN metrics_wide m ON m.period = e.period AND m.scope = e.scope
    LEFT JOIN adj ON adj.period = e.period AND adj.scope = e.scope
)
SELECT
    engagement_id,
    engagement_status,
    period,
    scope,
    revenue,
    reported_ebitda,
    accepted_adjustments,
    CAST(reported_ebitda + accepted_adjustments AS DECIMAL(38, 2)) AS adjusted_ebitda,
    div6(to_paise(reported_ebitda), to_paise(revenue)) AS reported_ebitda_margin,
    div6(to_paise(reported_ebitda + accepted_adjustments), to_paise(revenue)) AS adjusted_ebitda_margin,
    proposed_adjustments,
    accepted_count,
    proposed_count,
    rejected_count
FROM figures;

-- The EBITDA bridge (long, for display), per period and scope of the latest engagement, in step order:
--   step 0                'reported'    Reported EBITDA;
--   steps 1..n            'adjustment'  each accepted adjustment, by adj_id (adj_id, category, account, its
--                                       description, amount);
--   step n + 1            'adjusted'    Adjusted EBITDA.
-- Proposed and rejected adjustments are not steps (adjusted_ebitda shows the proposed sum).
CREATE VIEW ebitda_bridge AS
SELECT
    engagement_id, period, scope, 0 AS step, 'reported' AS kind, NULL::INTEGER AS adj_id, NULL::VARCHAR AS category,
    NULL::VARCHAR AS account, 'Reported EBITDA' AS description, reported_ebitda AS amount
FROM adjusted_ebitda
UNION ALL
SELECT
    a.engagement_id, a.period, a.scope,
    CAST(row_number() OVER (PARTITION BY a.period, a.scope ORDER BY a.adj_id) AS INTEGER),
    'adjustment', a.adj_id, a.category, a.account, a.description, CAST(a.amount AS DECIMAL(38, 2))
FROM adjustments a
JOIN engagement_periods e ON e.engagement_id = a.engagement_id AND e.period = a.period AND e.scope = a.scope
WHERE a.status = 'accepted'
UNION ALL
SELECT
    engagement_id, period, scope, CAST(accepted_count + 1 AS INTEGER), 'adjusted', NULL, NULL, NULL,
    'Adjusted EBITDA', adjusted_ebitda
FROM adjusted_ebitda;

-- The EBITDA-to-cash walk, one row per period and scope of the latest engagement. From the cash flow statement's
-- operating section (cash flow accounts in templates/dictionary/schedule_iii.csv; outflows are negative):
--   ebitda                  reported EBITDA (as metrics); adjustments: the accepted sum; adjusted_ebitda: their sum
--   non_cash_and_other      cf_op_profit_before_wc - ebitda (non-cash items, other income and the rest that
--                           separates EBITDA from operating profit before working capital changes)
--   working_capital_change  cf_cash_generated - cf_op_profit_before_wc
--   taxes_paid              cf_taxes_paid. When the statement maps no taxes-paid line: 0 if CFO equals cash
--                           generated from operations (no line between them, so no tax line), else NULL (a line is
--                           there but not mapped; it is not guessed)
--   other_operating         cfo - cf_cash_generated - taxes_paid (anything else printed between cash generated and
--                           CFO), so the walk lands on CFO
--   cfo                     net cash from operating activities
--   capex                   cf_capex (negative); free_cash_flow = cfo + capex (the free_cash_flow metric)
--   cash_conversion         cfo / ebitda (the cfo_to_ebitda metric); adjusted_cash_conversion: cfo / adjusted_ebitda
-- When every input is present: ebitda + non_cash_and_other + working_capital_change + taxes_paid + other_operating
-- = cfo, exactly. A missing input makes the lines that use it NULL; no difference is folded into another line.
-- The printed inputs (cf_op_profit_before_wc, cf_cash_generated, cf_taxes_paid) are the last columns, so a NULL line
-- shows which input is missing.
CREATE VIEW ebitda_to_cash AS
WITH cash AS (
    SELECT
        period,
        scope,
        max(value) FILTER (WHERE account = 'cf_op_profit_before_wc') AS cf_op_profit_before_wc,
        max(value) FILTER (WHERE account = 'cf_cash_generated') AS cf_cash_generated,
        max(value) FILTER (WHERE account = 'cf_taxes_paid') AS cf_taxes_paid,
        max(value) FILTER (WHERE account = 'cf_capex') AS cf_capex
    FROM current_facts
    WHERE period_months = 12
    GROUP BY period, scope
),
walk AS (
    SELECT
        a.engagement_id,
        a.period,
        a.scope,
        a.reported_ebitda AS ebitda,
        a.accepted_adjustments AS adjustments,
        a.adjusted_ebitda,
        c.cf_op_profit_before_wc - a.reported_ebitda AS non_cash_and_other,
        c.cf_cash_generated - c.cf_op_profit_before_wc AS working_capital_change,
        CASE
            WHEN c.cf_taxes_paid IS NOT NULL THEN c.cf_taxes_paid
            WHEN m.cfo = c.cf_cash_generated THEN 0
        END AS taxes_paid,
        CAST(m.cfo AS DECIMAL(38, 2)) AS cfo,
        c.cf_capex AS capex,
        CAST(m.free_cash_flow AS DECIMAL(38, 2)) AS free_cash_flow,
        m.cfo_to_ebitda AS cash_conversion,
        c.cf_op_profit_before_wc,
        c.cf_cash_generated,
        c.cf_taxes_paid
    FROM adjusted_ebitda a
    LEFT JOIN metrics_wide m ON m.period = a.period AND m.scope = a.scope
    LEFT JOIN cash c ON c.period = a.period AND c.scope = a.scope
)
SELECT
    engagement_id,
    period,
    scope,
    ebitda,
    adjustments,
    adjusted_ebitda,
    CAST(non_cash_and_other AS DECIMAL(38, 2)) AS non_cash_and_other,
    CAST(working_capital_change AS DECIMAL(38, 2)) AS working_capital_change,
    CAST(taxes_paid AS DECIMAL(38, 2)) AS taxes_paid,
    CAST(cfo - cf_cash_generated - taxes_paid AS DECIMAL(38, 2)) AS other_operating,
    cfo,
    CAST(capex AS DECIMAL(38, 2)) AS capex,
    free_cash_flow,
    cash_conversion,
    div6(to_paise(cfo), to_paise(adjusted_ebitda)) AS adjusted_cash_conversion,
    CAST(cf_op_profit_before_wc AS DECIMAL(38, 2)) AS cf_op_profit_before_wc,
    CAST(cf_cash_generated AS DECIMAL(38, 2)) AS cf_cash_generated,
    CAST(cf_taxes_paid AS DECIMAL(38, 2)) AS cf_taxes_paid
FROM walk;
