-- Metrics (PLAN.md Phase 7): plain views over facts, so they are always current and never stored twice.
--
-- Inputs: current_facts holds the facts a metric may use: confirmed, with an account, from a document whose
-- status is 'loaded', exactly one per account, fiscal year and scope. When several current documents report the
-- same account, year and scope (a later annual report repeats last year as a comparative), the fact comes from the
-- document whose own fiscal year it is (documents.fiscal_year = facts.fiscal_year); otherwise from the latest
-- report (highest documents.fiscal_year, then the latest ingest). Queries should use current_facts too.
--
-- Definitions (tests/fixtures/sample-financials/answer_key.json "conventions"):
-- - Balance sheet items are closing balances of the year; days use a 365-day year.
-- - EBITDA = PBT + finance cost + depreciation - other income.
-- - Debt = long-term borrowings + short-term borrowings.
-- - Inventory days and payable days are on cost of materials consumed.
-- - Cash flow outflows are negative, so capex (cf_capex) is negative.
-- - Each metric is per fiscal year and scope. Standalone and consolidated are never mixed.
--
-- Rules:
-- - A metric whose inputs are missing, or whose denominator is zero, is NULL. Nothing is guessed.
-- - Money metrics (ebitda, free_cash_flow) are exact sums in rupees.
-- - Ratios and days are computed exactly on integer paise and rounded once, half away from zero, to 6
--   decimal places (div6 below). DuckDB divides DECIMALs as DOUBLE, so div6 uses integer arithmetic instead.
--   A ratio is a fraction: 0.180000 means 18%.
-- - metrics.value is DECIMAL(38, 6) for every metric; metrics.unit says rupees, ratio or days.

-- round(n / d, 6), half away from zero, exactly. n and d are HUGEINT; NULL when d is 0 or either is NULL.
CREATE MACRO div6(n, d) AS
    CASE WHEN d <> 0 THEN CAST(
        CAST(sign(n) * sign(d) * ((2 * abs(n) * 1000000 + abs(d)) // (2 * abs(d))) AS DECIMAL(38, 0))
            * CAST(0.000001 AS DECIMAL(7, 6))
        AS DECIMAL(38, 6))
    END;

-- Integer paise -> rupees, exactly.
CREATE MACRO rupees(p) AS CAST(CAST(p AS DECIMAL(38, 0)) * CAST(0.01 AS DECIMAL(3, 2)) AS DECIMAL(38, 6));

-- The fiscal year before fy: 'FY25' -> 'FY24'.
CREATE MACRO prior_fy(fy) AS printf('FY%02d', (CAST(substr(fy, 3) AS INTEGER) + 99) % 100);

CREATE VIEW current_facts AS
SELECT f.fact_id, f.doc_id, f.account, f.fiscal_year, f.period_end, f.scope, f.value
FROM facts f
JOIN documents d ON d.doc_id = f.doc_id
WHERE d.status = 'loaded' AND f.confirmed AND f.account IS NOT NULL
QUALIFY row_number() OVER (
    PARTITION BY f.account, f.fiscal_year, f.scope
    ORDER BY d.fiscal_year = f.fiscal_year DESC, d.fiscal_year DESC, d.doc_id DESC, f.fact_id
) = 1;

-- Which accounts each metric uses, for metric_inputs. Keep it in step with the formulas in the metrics view.
-- prior_year: the input is the previous fiscal year's value.
-- stands_in_for: total current assets and total current liabilities are the reported total when the document has
-- that line; otherwise the sum of the current lines it does report (older formats print no subtotal). These rows
-- are the lines summed in that case.
CREATE VIEW metric_definitions AS
WITH ebitda(accounts) AS (SELECT ['pbt', 'finance_cost', 'depreciation', 'other_income']),
debt(accounts) AS (SELECT ['lt_borrowings', 'st_borrowings']),
direct(metric, accounts) AS (
    SELECT * FROM (VALUES
        ('revenue_growth', ['revenue']),
        ('gross_margin', ['revenue', 'cost_of_materials']),
        ('ebitda', (FROM ebitda)),
        ('ebitda_margin', list_concat((FROM ebitda), ['revenue'])),
        ('pat_margin', ['pat', 'revenue']),
        ('cfo_to_ebitda', list_concat((FROM ebitda), ['cfo'])),
        ('free_cash_flow', ['cfo', 'cf_capex']),
        ('capex_to_revenue', ['cf_capex', 'revenue']),
        ('receivable_days', ['trade_receivables', 'revenue']),
        ('inventory_days', ['inventories', 'cost_of_materials']),
        ('payable_days', ['trade_payables', 'cost_of_materials']),
        ('cash_conversion_cycle', ['trade_receivables', 'revenue', 'inventories', 'trade_payables',
            'cost_of_materials']),
        ('debt_to_ebitda', list_concat((FROM debt), (FROM ebitda))),
        ('interest_cover', (FROM ebitda)),
        ('current_ratio', ['total_current_assets', 'total_current_liabilities']),
        ('debt_to_equity', list_concat((FROM debt), ['total_equity'])),
        ('roce', list_concat((FROM debt), ['pbt', 'finance_cost', 'total_equity'])),
        ('roe', ['pat', 'total_equity'])
    )
)
SELECT metric, unnest(accounts) AS account, false AS prior_year, NULL::VARCHAR AS stands_in_for FROM direct
UNION ALL
SELECT 'revenue_growth', 'revenue', true, NULL
UNION ALL
SELECT 'current_ratio', unnest(['inventories', 'current_investments', 'trade_receivables', 'cash',
    'other_bank_balances', 'other_current_assets']), false, 'total_current_assets'
UNION ALL
SELECT 'current_ratio', unnest(['st_borrowings', 'lease_liabilities_current', 'trade_payables',
    'other_financial_liabilities_current', 'other_current_liabilities', 'st_provisions',
    'current_tax_liabilities']), false, 'total_current_liabilities';

-- One row per fiscal year, scope and metric (long format). Every metric appears for every year and scope that has
-- any current fact, with value NULL when an input is missing.
CREATE VIEW metrics AS
WITH paise AS (
    -- Values as integer paise (exact: value has two decimal places).
    SELECT account, fiscal_year, scope, period_end, CAST(value * 100 AS HUGEINT) AS p
    FROM current_facts
),
year_inputs AS (
    SELECT
        fiscal_year,
        scope,
        max(period_end) AS period_end,
        max(p) FILTER (WHERE account = 'revenue') AS revenue,
        max(p) FILTER (WHERE account = 'other_income') AS other_income,
        max(p) FILTER (WHERE account = 'cost_of_materials') AS cost_of_materials,
        max(p) FILTER (WHERE account = 'finance_cost') AS finance_cost,
        max(p) FILTER (WHERE account = 'depreciation') AS depreciation,
        max(p) FILTER (WHERE account = 'pbt') AS pbt,
        max(p) FILTER (WHERE account = 'pat') AS pat,
        max(p) FILTER (WHERE account = 'total_equity') AS total_equity,
        max(p) FILTER (WHERE account = 'lt_borrowings') AS lt_borrowings,
        max(p) FILTER (WHERE account = 'st_borrowings') AS st_borrowings,
        max(p) FILTER (WHERE account = 'trade_payables') AS trade_payables,
        -- The reported total, else the sum of the current liability lines reported (see metric_definitions).
        coalesce(
            max(p) FILTER (WHERE account = 'total_current_liabilities'),
            CAST(sum(p) FILTER (WHERE account IN ('st_borrowings', 'lease_liabilities_current', 'trade_payables',
                'other_financial_liabilities_current', 'other_current_liabilities', 'st_provisions',
                'current_tax_liabilities')) AS HUGEINT)
        ) AS current_liabilities,
        max(p) FILTER (WHERE account = 'inventories') AS inventories,
        max(p) FILTER (WHERE account = 'trade_receivables') AS trade_receivables,
        -- The reported total, else the sum of the current asset lines reported.
        coalesce(
            max(p) FILTER (WHERE account = 'total_current_assets'),
            CAST(sum(p) FILTER (WHERE account IN ('inventories', 'current_investments', 'trade_receivables', 'cash',
                'other_bank_balances', 'other_current_assets')) AS HUGEINT)
        ) AS current_assets,
        max(p) FILTER (WHERE account = 'cfo') AS cfo,
        max(p) FILTER (WHERE account = 'cf_capex') AS cf_capex
    FROM paise
    GROUP BY fiscal_year, scope
),
derived AS (
    SELECT
        y.*,
        prev.revenue AS prior_revenue,
        y.pbt + y.finance_cost + y.depreciation - y.other_income AS ebitda,  -- EBITDA (conventions)
        y.lt_borrowings + y.st_borrowings AS debt,                           -- Debt (conventions)
        y.pbt + y.finance_cost AS ebit                                       -- EBIT, for ROCE
    FROM year_inputs y
    LEFT JOIN year_inputs prev ON prev.scope = y.scope AND prev.fiscal_year = prior_fy(y.fiscal_year)
),
wide AS (
    SELECT
        fiscal_year,
        scope,
        period_end,
        -- Growth: revenue / prior-year revenue - 1. NULL without the prior year.
        div6(revenue - prior_revenue, prior_revenue) AS revenue_growth,
        -- Profitability: (revenue - cost of materials) / revenue; EBITDA / revenue; PAT / revenue.
        div6(revenue - cost_of_materials, revenue) AS gross_margin,
        rupees(ebitda) AS ebitda,
        div6(ebitda, revenue) AS ebitda_margin,
        div6(pat, revenue) AS pat_margin,
        -- Cash: CFO / EBITDA; free cash flow = CFO + capex (capex is negative); capex / revenue as a
        -- positive fraction (-capex / revenue).
        div6(cfo, ebitda) AS cfo_to_ebitda,
        rupees(cfo + cf_capex) AS free_cash_flow,
        div6(-cf_capex, revenue) AS capex_to_revenue,
        -- Working capital, closing balances, 365-day year: receivables on revenue; inventory and payables on
        -- cost of materials. Cash conversion cycle = receivable + inventory - payable days, computed from the
        -- exact fractions and rounded once.
        div6(trade_receivables * 365, revenue) AS receivable_days,
        div6(inventories * 365, cost_of_materials) AS inventory_days,
        div6(trade_payables * 365, cost_of_materials) AS payable_days,
        div6(
            trade_receivables * 365 * cost_of_materials + (inventories - trade_payables) * 365 * revenue,
            revenue * cost_of_materials
        ) AS cash_conversion_cycle,
        -- Leverage and returns. Interest cover = EBITDA / finance cost. ROCE = EBIT / (total equity + debt),
        -- with EBIT = PBT + finance cost. ROE = PAT / total equity. Closing balances throughout.
        div6(debt, ebitda) AS debt_to_ebitda,
        div6(ebitda, finance_cost) AS interest_cover,
        div6(current_assets, current_liabilities) AS current_ratio,
        div6(debt, total_equity) AS debt_to_equity,
        div6(ebit, total_equity + debt) AS roce,
        div6(pat, total_equity) AS roe
    FROM derived
)
SELECT
    fiscal_year,
    period_end,
    scope,
    metric,
    value,
    CASE
        WHEN metric IN ('ebitda', 'free_cash_flow') THEN 'rupees'
        WHEN metric IN ('receivable_days', 'inventory_days', 'payable_days', 'cash_conversion_cycle') THEN 'days'
        ELSE 'ratio'
    END AS unit
FROM wide UNPIVOT INCLUDE NULLS (
    value FOR metric IN (
        revenue_growth, gross_margin, ebitda, ebitda_margin, pat_margin, cfo_to_ebitda, free_cash_flow,
        capex_to_revenue, receivable_days, inventory_days, payable_days, cash_conversion_cycle, debt_to_ebitda,
        interest_cover, current_ratio, debt_to_equity, roce, roe
    )
);

-- Lineage: metric -> the facts it used, one row per input. fact_id is NULL when a required input is missing (which
-- is why the metric is NULL). input_fiscal_year is the prior year for revenue_growth's prior revenue. When total
-- current assets or liabilities is not reported, its row has fact_id NULL and the reported lines summed in its
-- place follow (rows with stands_in_for set appear only then).
CREATE VIEW metric_inputs AS
SELECT
    m.fiscal_year,
    m.scope,
    m.metric,
    d.account,
    d.stands_in_for,
    CASE WHEN d.prior_year THEN prior_fy(m.fiscal_year) ELSE m.fiscal_year END AS input_fiscal_year,
    f.fact_id,
    f.doc_id,
    f.value
FROM metrics m
JOIN metric_definitions d ON d.metric = m.metric
LEFT JOIN current_facts f
    ON f.scope = m.scope
    AND f.account = d.account
    AND f.fiscal_year = CASE WHEN d.prior_year THEN prior_fy(m.fiscal_year) ELSE m.fiscal_year END
WHERE d.stands_in_for IS NULL
    OR (
        f.fact_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM current_facts t
            WHERE t.account = d.stands_in_for AND t.scope = m.scope AND t.fiscal_year = m.fiscal_year
        )
    );
