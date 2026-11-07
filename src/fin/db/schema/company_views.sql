-- Metrics (PLAN.md Phase 7): macros and plain views over facts, so they are always current and never stored twice.
-- THIS FILE IS WHERE A METRIC IS ADDED OR CHANGED: edit `metric_definitions` (inputs), the `metrics` view (formula) and
-- the CASE for its unit. See docs/CUSTOMISING.md.
--
-- Inputs: current_facts holds the facts a metric may use (see below). Queries should use current_facts too.
--
-- Definitions (tests/fixtures/sample-financials/answer_key.json "conventions"):
-- - Balance sheet items are closing balances of the year; days use a 365-day year.
-- - EBITDA = PBT + finance cost + depreciation - other income.
-- - Debt = long-term borrowings + short-term borrowings.
-- - Inventory days and payable days are on cost of materials consumed.
-- - Cash flow outflows are negative, so capex (cf_capex) is negative.
-- - Each metric is per period and scope. Standalone and consolidated are never mixed.
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

-- The same period a year before: 'FY26' -> 'FY25', 'FY26-Q1' -> 'FY25-Q1'.
CREATE MACRO prior_period(p) AS
    prior_fy(split_part(p, '-', 1)) || CASE WHEN contains(p, '-') THEN '-' || split_part(p, '-', 2) ELSE '' END;

-- The quarter before a quarter: 'FY26-Q1' -> 'FY25-Q4', 'FY26-Q3' -> 'FY26-Q2'; NULL for any other period.
CREATE MACRO previous_quarter(p) AS
    CASE
        WHEN p LIKE '%-Q1' THEN prior_fy(split_part(p, '-', 1)) || '-Q4'
        WHEN regexp_matches(p, '-Q[234]$') THEN split_part(p, '-', 1) || '-Q' || (CAST(right(p, 1) AS INTEGER) - 1)
    END;

-- One value per document, account, period and scope. When a document maps two or more lines to the same account
-- (trade payables printed as "dues of micro and small enterprises" and "dues of others"), the value is their sum.
-- A line equal to the sum of the other lines is their total ("Trade payables" above its two parts): then the total
-- alone is used, so nothing is counted twice. fact_ids lists the lines used; fact_id is the first of them.
CREATE VIEW document_facts AS
WITH lines AS (
    SELECT
        f.doc_id, f.account, f.fiscal_year, f.period, f.period_months, f.period_end, f.scope, f.fact_id, f.value,
        sum(f.value) OVER (PARTITION BY f.doc_id, f.account, f.period, f.scope) AS lines_total
    FROM facts f
    WHERE f.confirmed AND f.account IS NOT NULL
),
totals AS (
    SELECT doc_id, account, period, scope, min(fact_id) AS total_id
    FROM lines
    WHERE value * 2 = lines_total
    GROUP BY doc_id, account, period, scope
)
SELECT
    l.doc_id,
    l.account,
    any_value(l.fiscal_year) AS fiscal_year,
    l.period,
    any_value(l.period_months) AS period_months,
    max(l.period_end) AS period_end,
    l.scope,
    min(l.fact_id) AS fact_id,
    list(l.fact_id ORDER BY l.fact_id) AS fact_ids,
    sum(l.value) AS value
FROM lines l
LEFT JOIN totals t USING (doc_id, account, period, scope)
WHERE t.total_id IS NULL OR l.fact_id = t.total_id
GROUP BY l.doc_id, l.account, l.period, l.scope;

-- The facts a metric may use: from a document whose status is 'loaded', exactly one value per account, period and
-- scope (summed per document_facts). Quarter, half-year and year values never mix: they are different periods.
-- When several current documents report the same account, period and scope (a later report repeats a period as a
-- comparative), the value comes from the document whose own period it is (documents.period_end = the period's
-- end); otherwise from the latest report (latest documents.period_end, then the latest ingest).
CREATE VIEW current_facts AS
SELECT
    x.fact_id, x.fact_ids, x.doc_id, x.account, x.fiscal_year, x.period, x.period_months, x.period_end, x.scope,
    x.value
FROM document_facts x
JOIN documents d ON d.doc_id = x.doc_id
WHERE d.status = 'loaded'
QUALIFY row_number() OVER (
    PARTITION BY x.account, x.period, x.scope
    ORDER BY d.period_end = x.period_end DESC, d.period_end DESC, d.doc_id DESC
) = 1;

-- Which accounts each metric uses, for metric_inputs. Keep it in step with the formulas in the metrics view.
-- prior_year: the input is the same period a year before. prior_quarter: the input is the quarter before.
-- stands_in_for: total current assets and total current liabilities are the reported total when the document has
-- that line; otherwise the sum of the current lines it does report (older formats print no subtotal). These rows
-- are the lines summed in that case.
CREATE VIEW metric_definitions AS
WITH ebitda(accounts) AS (SELECT ['pbt', 'finance_cost', 'depreciation', 'other_income']),
debt(accounts) AS (SELECT ['lt_borrowings', 'st_borrowings']),
direct(metric, accounts) AS (
    SELECT * FROM (VALUES
        ('revenue', ['revenue']),
        ('pat', ['pat']),
        ('revenue_growth', ['revenue']),
        ('revenue_growth_qoq', ['revenue']),
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
SELECT metric, unnest(accounts) AS account, false AS prior_year, false AS prior_quarter,
    NULL::VARCHAR AS stands_in_for
FROM direct
UNION ALL
SELECT 'revenue_growth', 'revenue', true, false, NULL
UNION ALL
SELECT 'revenue_growth_qoq', 'revenue', false, true, NULL
UNION ALL
SELECT 'current_ratio', unnest(['inventories', 'current_investments', 'trade_receivables', 'cash',
    'other_bank_balances', 'other_current_assets']), false, false, 'total_current_assets'
UNION ALL
SELECT 'current_ratio', unnest(['st_borrowings', 'lease_liabilities_current', 'trade_payables',
    'other_financial_liabilities_current', 'other_current_liabilities', 'st_provisions',
    'current_tax_liabilities']), false, false, 'total_current_liabilities';

-- Metrics, one row per period, scope and metric (long format), with value NULL when an input is missing.
--
-- Annual periods (period_months = 12) get the annual metrics, by the definitions and rules above. Every annual
-- metric appears for every fiscal year and scope with any current fact.
--
-- Quarter, half-year and nine-month periods get the metrics that make sense on one period's flows: revenue, PAT,
-- EBITDA, gross / EBITDA / PAT margin, revenue_growth (year on year: against the same period a year before) and,
-- for quarters, revenue_growth_qoq (against the quarter before; Q1 against the previous year's Q4). Ratios that
-- need a full year of flows or balance-sheet averages (days, returns, leverage, cash conversion) are left out for
-- these periods: a quarter's revenue against a closing balance is not comparable to the annual figure.
CREATE VIEW metrics AS
WITH paise AS (
    -- Values as integer paise (exact: value has two decimal places).
    SELECT account, fiscal_year, period, period_months, scope, period_end, CAST(value * 100 AS HUGEINT) AS p
    FROM current_facts
),
period_inputs AS (
    SELECT
        fiscal_year,
        period,
        period_months,
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
    GROUP BY fiscal_year, period, period_months, scope
),
derived AS (
    SELECT
        y.*,
        prev.revenue AS prior_revenue,
        before.revenue AS previous_quarter_revenue,
        y.pbt + y.finance_cost + y.depreciation - y.other_income AS ebitda,  -- EBITDA (conventions)
        y.lt_borrowings + y.st_borrowings AS debt,                           -- Debt (conventions)
        y.pbt + y.finance_cost AS ebit                                       -- EBIT, for ROCE
    FROM period_inputs y
    LEFT JOIN period_inputs prev ON prev.scope = y.scope AND prev.period = prior_period(y.period)
    LEFT JOIN period_inputs before ON before.scope = y.scope AND before.period = previous_quarter(y.period)
),
wide AS (
    SELECT
        fiscal_year,
        period,
        period_months,
        scope,
        period_end,
        rupees(revenue) AS revenue,
        rupees(pat) AS pat,
        -- Growth: revenue / revenue of the same period a year before - 1; for quarters also against the quarter
        -- before. NULL without that period.
        div6(revenue - prior_revenue, prior_revenue) AS revenue_growth,
        div6(revenue - previous_quarter_revenue, previous_quarter_revenue) AS revenue_growth_qoq,
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
),
long AS (
    FROM wide UNPIVOT INCLUDE NULLS (
        value FOR metric IN (
            revenue, pat, revenue_growth, revenue_growth_qoq, gross_margin, ebitda, ebitda_margin, pat_margin,
            cfo_to_ebitda, free_cash_flow, capex_to_revenue, receivable_days, inventory_days, payable_days,
            cash_conversion_cycle, debt_to_ebitda, interest_cover, current_ratio, debt_to_equity, roce, roe
        )
    )
)
SELECT
    fiscal_year,
    period,
    period_months,
    period_end,
    scope,
    metric,
    value,
    CASE
        WHEN metric IN ('revenue', 'pat', 'ebitda', 'free_cash_flow') THEN 'rupees'
        WHEN metric IN ('receivable_days', 'inventory_days', 'payable_days', 'cash_conversion_cycle') THEN 'days'
        ELSE 'ratio'
    END AS unit
FROM long
WHERE CASE
    WHEN period_months = 12 THEN metric NOT IN ('revenue', 'pat', 'revenue_growth_qoq')
    WHEN period_months = 3 THEN metric IN ('revenue', 'pat', 'revenue_growth', 'revenue_growth_qoq', 'gross_margin',
        'ebitda', 'ebitda_margin', 'pat_margin')
    ELSE metric IN ('revenue', 'pat', 'revenue_growth', 'gross_margin', 'ebitda', 'ebitda_margin', 'pat_margin')
END;

-- Lineage: metric -> the facts it used, one row per input line (an account a document prints on two lines has two
-- rows). fact_id is NULL when a required input is missing (which is why the metric is NULL). input_period is the
-- period the input comes from: the same period a year before for revenue_growth's prior revenue, the quarter before
-- for revenue_growth_qoq. When total current assets or liabilities is not reported, its row has fact_id NULL and
-- the reported lines summed in its place follow (rows with stands_in_for set appear only then).
CREATE VIEW metric_inputs AS
WITH wanted AS (
    SELECT
        m.fiscal_year,
        m.period,
        m.scope,
        m.metric,
        d.account,
        d.stands_in_for,
        CASE
            WHEN d.prior_year THEN prior_period(m.period)
            WHEN d.prior_quarter THEN previous_quarter(m.period)
            ELSE m.period
        END AS input_period
    FROM metrics m
    JOIN metric_definitions d ON d.metric = m.metric
)
SELECT
    w.fiscal_year,
    w.period,
    w.scope,
    w.metric,
    w.account,
    w.stands_in_for,
    split_part(w.input_period, '-', 1) AS input_fiscal_year,
    w.input_period,
    line.fact_id,
    f.doc_id,
    line.value
FROM wanted w
LEFT JOIN current_facts f ON f.scope = w.scope AND f.account = w.account AND f.period = w.input_period
LEFT JOIN facts line ON list_contains(f.fact_ids, line.fact_id)
WHERE w.stands_in_for IS NULL
    OR (
        f.fact_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM current_facts t
            WHERE t.account = w.stands_in_for AND t.scope = w.scope AND t.period = w.period
        )
    );
