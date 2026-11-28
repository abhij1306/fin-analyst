-- Part of the company schema. Runs after company_metric_inputs.sql.

-- The metric formulas: one row per period and scope, one column per metric (every column after period_end).
-- Every period gets every column here; the metrics view below keeps the periods each metric is for.
CREATE VIEW metrics_wide AS
WITH printed AS (
    -- Values as integer paise (exact: value has two decimal places).
    SELECT account, fiscal_year, period, period_months, scope, period_end, CAST(value * 100 AS HUGEINT) AS p
    FROM current_facts
),
paise AS (
    -- Plus, for a total in stand_ins the document does not print, the signed sum of its lines printed (a line is left
    -- out when the line it is part of is printed).
    FROM printed
    UNION ALL
    SELECT s.total, x.fiscal_year, x.period, x.period_months, x.scope, max(x.period_end),
        CAST(sum(x.p * s.sign) AS HUGEINT)
    FROM printed x
    JOIN stand_ins s ON s.account = x.account
    WHERE NOT EXISTS (
        SELECT 1 FROM printed t
        WHERE t.account IN (s.total, s.part_of) AND t.period = x.period AND t.scope = x.scope
    )
    GROUP BY s.total, x.fiscal_year, x.period, x.period_months, x.scope
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
        max(p) FILTER (WHERE account = 'total_income') AS total_income,
        max(p) FILTER (WHERE account = 'total_expenses') AS total_expenses,
        max(p) FILTER (WHERE account = 'cost_of_materials') AS cost_of_materials,
        max(p) FILTER (WHERE account = 'purchases_stock_in_trade') AS purchases_stock_in_trade,
        max(p) FILTER (WHERE account = 'change_in_inventories') AS change_in_inventories,
        max(p) FILTER (WHERE account = 'finance_cost') AS finance_cost,
        max(p) FILTER (WHERE account = 'depreciation') AS depreciation,
        max(p) FILTER (WHERE account = 'profit_before_exceptional_items') AS profit_before_exceptional_items,
        max(p) FILTER (WHERE account = 'exceptional_items') AS exceptional_items,
        max(p) FILTER (WHERE account = 'extraordinary_items') AS extraordinary_items,
        max(p) FILTER (WHERE account = 'share_of_associates_jv') AS share_of_associates_jv,
        max(p) FILTER (WHERE account = 'pbt') AS pbt,
        max(p) FILTER (WHERE account = 'total_tax_expense') AS tax,
        max(p) FILTER (WHERE account = 'pat') AS pat,
        max(p) FILTER (WHERE account = 'eps_basic') AS eps_basic,
        max(p) FILTER (WHERE account = 'eps_diluted') AS eps_diluted,
        max(p) FILTER (WHERE account = 'interest_income') AS interest_income,
        max(p) FILTER (WHERE account = 'interest_expended') AS interest_expended,
        max(p) FILTER (WHERE account = 'impairment_on_financial_instruments') AS impairment,
        max(p) FILTER (WHERE account = 'provisions_and_contingencies') AS provisions_and_contingencies,
        max(p) FILTER (WHERE account = 'total_equity') AS total_equity,
        max(p) FILTER (WHERE account = 'share_capital') AS share_capital,
        max(p) FILTER (WHERE account = 'other_equity') AS other_equity,
        max(p) FILTER (WHERE account = 'total_assets') AS total_assets,
        max(p) FILTER (WHERE account = 'ppe_net') AS ppe_net,
        max(p) FILTER (WHERE account = 'loans') AS loans,
        max(p) FILTER (WHERE account = 'cash') AS cash,
        max(p) FILTER (WHERE account = 'lt_borrowings') AS lt_borrowings,
        max(p) FILTER (WHERE account = 'st_borrowings') AS st_borrowings,
        max(p) FILTER (WHERE account = 'trade_payables') AS trade_payables,
        max(p) FILTER (WHERE account = 'total_current_liabilities') AS current_liabilities,
        max(p) FILTER (WHERE account = 'inventories') AS inventories,
        max(p) FILTER (WHERE account = 'trade_receivables') AS trade_receivables,
        max(p) FILTER (WHERE account = 'total_current_assets') AS current_assets,
        max(p) FILTER (WHERE account = 'cfo') AS cfo,
        max(p) FILTER (WHERE account = 'cf_capex') AS cf_capex
    FROM paise
    GROUP BY fiscal_year, period, period_months, scope
),
base AS (
    SELECT
        *,
        -- EBITDA (conventions): from PBT before exceptional and extraordinary items.
        CASE
            WHEN coalesce(exceptional_items, 0) = 0 AND coalesce(extraordinary_items, 0) = 0 THEN pbt
            ELSE profit_before_exceptional_items + coalesce(share_of_associates_jv, 0)
        END + finance_cost + depreciation - other_income AS ebitda,
        lt_borrowings + st_borrowings AS debt,                       -- Debt (conventions)
        pbt + finance_cost AS ebit,                                  -- EBIT (conventions)
        -- Lenders: an NBFC prints interest income; a bank prints interest expended (its interest earned is
        -- revenue). Interest expense is a bank's interest expended, else finance costs.
        interest_income IS NOT NULL OR interest_expended IS NOT NULL AS lender,
        coalesce(interest_income, CASE WHEN interest_expended IS NOT NULL THEN revenue END) AS interest_earned,
        coalesce(interest_expended, finance_cost) AS interest_expense,
        coalesce(impairment, provisions_and_contingencies) AS credit_charge,
        coalesce(total_equity, share_capital + other_equity) AS lender_equity
    FROM period_inputs
),
derived AS (
    SELECT
        y.*,
        prev.revenue AS prev_revenue,
        prev.ebitda AS prev_ebitda,
        prev.pat AS prev_pat,
        prev.total_assets AS prev_total_assets,
        prev.total_equity AS prev_total_equity,
        prev.debt AS prev_debt,
        prev.loans AS prev_loans,
        q1.revenue AS previous_quarter_revenue,
        y3.revenue AS revenue_3y, y3.ebitda AS ebitda_3y, y3.pat AS pat_3y,
        y3.total_assets AS total_assets_3y, y3.total_equity AS total_equity_3y,
        y5.revenue AS revenue_5y, y5.ebitda AS ebitda_5y, y5.pat AS pat_5y,
        y5.total_assets AS total_assets_5y, y5.total_equity AS total_equity_5y,
        y.revenue + q1.revenue + q2.revenue + q3.revenue AS ttm_revenue,
        y.ebitda + q1.ebitda + q2.ebitda + q3.ebitda AS ttm_ebitda,
        y.pat + q1.pat + q2.pat + q3.pat AS ttm_pat
    FROM base y
    LEFT JOIN base prev ON prev.scope = y.scope AND prev.period = prior_period(y.period)
    LEFT JOIN base q1 ON q1.scope = y.scope AND q1.period = previous_quarter(y.period)
    LEFT JOIN base q2 ON q2.scope = y.scope AND q2.period = previous_quarter(previous_quarter(y.period))
    LEFT JOIN base q3 ON q3.scope = y.scope
        AND q3.period = previous_quarter(previous_quarter(previous_quarter(y.period)))
    LEFT JOIN base y3 ON y3.scope = y.scope AND y.period_months = 12 AND y3.period = fy_back(y.period, 3)
    LEFT JOIN base y5 ON y5.scope = y.scope AND y.period_months = 12 AND y5.period = fy_back(y.period, 5)
)
SELECT
    fiscal_year,
    period,
    period_months,
    scope,
    period_end,
    -- headline
    rupees(revenue) AS revenue,
    rupees(pat) AS pat,
    -- profitability
    -- gross margin: changes in inventories as printed (an increase in inventories is negative, lowering the cost)
    div6(
        revenue - cost_of_materials - coalesce(purchases_stock_in_trade, 0) - coalesce(change_in_inventories, 0),
        revenue
    ) AS gross_margin,
    rupees(ebitda) AS ebitda,
    div6(ebitda, revenue) AS ebitda_margin,
    rupees(ebit) AS ebit,
    div6(ebit, revenue) AS ebit_margin,
    div6(pbt, revenue) AS pbt_margin,
    div6(pat, revenue) AS pat_margin,
    -- returns: closing balances; *_avg on the average of opening and closing (2x / (opening + closing))
    div6(pat, total_equity) AS roe,
    div6(ebit, total_equity + debt) AS roce,
    div6(pat, total_assets) AS roa,
    div6(2 * pat, total_equity + prev_total_equity) AS roe_avg,
    div6(2 * ebit, total_equity + debt + prev_total_equity + prev_debt) AS roce_avg,
    div6(2 * pat, total_assets + prev_total_assets) AS roa_avg,
    -- liquidity
    div6(current_assets, current_liabilities) AS current_ratio,
    div6(current_assets - inventories, current_liabilities) AS quick_ratio,
    div6(cash, current_liabilities) AS cash_ratio,
    -- solvency
    div6(debt, total_equity) AS debt_to_equity,
    div6(debt, ebitda) AS debt_to_ebitda,
    rupees(debt - cash) AS net_debt,
    div6(debt - cash, ebitda) AS net_debt_to_ebitda,
    div6(ebitda, finance_cost) AS interest_cover,
    div6(debt, total_assets) AS debt_to_assets,
    -- efficiency: closing balances, 365-day year. Cash conversion cycle = receivable + inventory - payable
    -- days, computed from the exact fractions and rounded once.
    div6(revenue, total_assets) AS asset_turnover,
    div6(revenue, ppe_net) AS fixed_asset_turnover,
    div6(trade_receivables * 365, revenue) AS receivable_days,
    div6(inventories * 365, cost_of_materials) AS inventory_days,
    div6(trade_payables * 365, cost_of_materials) AS payable_days,
    div6(
        trade_receivables * 365 * cost_of_materials + (inventories - trade_payables) * 365 * revenue,
        revenue * cost_of_materials
    ) AS cash_conversion_cycle,
    -- cash flow (capex is negative)
    rupees(cfo) AS cfo,
    rupees(cfo + cf_capex) AS free_cash_flow,
    div6(cfo, ebitda) AS cfo_to_ebitda,
    div6(cfo, pat) AS cfo_to_pat,
    rupees(pat - cfo) AS accruals,
    div6(pat - cfo, total_assets) AS accruals_to_assets,
    div6(-cf_capex, revenue) AS capex_to_revenue,
    div6(-cf_capex, depreciation) AS capex_to_depreciation,
    -- growth
    growth6(revenue, prev_revenue) AS revenue_growth,
    growth6(revenue, previous_quarter_revenue) AS revenue_growth_qoq,
    growth6(ebitda, prev_ebitda) AS ebitda_growth,
    growth6(pat, prev_pat) AS pat_growth,
    growth6(total_assets, prev_total_assets) AS total_assets_growth,
    growth6(total_equity, prev_total_equity) AS total_equity_growth,
    cagr6(revenue, revenue_3y, 3) AS revenue_cagr_3y,
    cagr6(revenue, revenue_5y, 5) AS revenue_cagr_5y,
    cagr6(ebitda, ebitda_3y, 3) AS ebitda_cagr_3y,
    cagr6(ebitda, ebitda_5y, 5) AS ebitda_cagr_5y,
    cagr6(pat, pat_3y, 3) AS pat_cagr_3y,
    cagr6(pat, pat_5y, 5) AS pat_cagr_5y,
    cagr6(total_assets, total_assets_3y, 3) AS total_assets_cagr_3y,
    cagr6(total_assets, total_assets_5y, 5) AS total_assets_cagr_5y,
    cagr6(total_equity, total_equity_3y, 3) AS total_equity_cagr_3y,
    cagr6(total_equity, total_equity_5y, 5) AS total_equity_cagr_5y,
    -- earnings quality
    div6(other_income, pbt) AS other_income_to_pbt,
    div6(exceptional_items, pbt) AS exceptional_items_to_pbt,
    div6(tax, pbt) AS effective_tax_rate,
    -- per share, as printed
    rupees(eps_basic) AS eps_basic,
    rupees(eps_diluted) AS eps_diluted,
    -- lenders
    CASE WHEN lender THEN rupees(interest_earned - interest_expense) END AS net_interest_income,
    CASE WHEN lender THEN div6(
        total_expenses - interest_expense - credit_charge, total_income - interest_expense
    ) END AS cost_to_income,
    CASE WHEN lender THEN div6(2 * credit_charge, loans + prev_loans) END AS credit_cost,
    CASE WHEN lender THEN div6(total_assets, lender_equity) END AS assets_to_equity,
    -- trailing twelve months
    rupees(ttm_revenue) AS ttm_revenue,
    rupees(ttm_ebitda) AS ttm_ebitda,
    rupees(ttm_pat) AS ttm_pat,
    div6(ttm_ebitda, ttm_revenue) AS ttm_ebitda_margin,
    div6(ttm_pat, ttm_revenue) AS ttm_pat_margin
FROM derived;

-- metrics_wide plus one column per saved metric (`fin metric add`). fin.metrics.saved rewrites this view from the
-- saved_metrics table whenever a saved metric is added or removed; with none it is metrics_wide.
CREATE VIEW saved_metrics_wide AS SELECT * FROM metrics_wide;

-- Metrics, one row per period, scope and metric (long format). Which periods get a metric, and whether a metric
-- without a value is a NULL row or no row, is in metric_definitions (periods, when_missing).
--
-- Annual periods (period_months = 12) get the annual metrics. Quarter, half-year and nine-month periods get the
-- metrics that make sense on one period's flows ('all' and 'interim'; 'quarter' for quarters only). Ratios that
-- need a full year of flows or balance-sheet averages (days, returns, leverage, cash conversion) are annual: a
-- quarter's revenue against a closing balance is not comparable to the annual figure.
-- about: Every metric, built-in and saved, one row per period, scope and metric; value is a fraction for a ratio.
-- example: SELECT period, scope, metric, value, unit FROM metrics WHERE metric = 'ebitda_margin' ORDER BY period
CREATE VIEW metrics AS
WITH long AS (
    FROM saved_metrics_wide UNPIVOT INCLUDE NULLS (
        value FOR metric IN (COLUMNS(* EXCLUDE (fiscal_year, period, period_months, scope, period_end)))
    )
)
SELECT
    l.fiscal_year,
    l.period,
    l.period_months,
    l.period_end,
    l.scope,
    l.metric,
    l.value,
    d.unit,
    d.category
FROM long l
JOIN metric_definitions d ON d.metric = l.metric
WHERE CASE d.periods
        WHEN 'annual' THEN l.period_months = 12
        WHEN 'interim' THEN l.period_months <> 12
        WHEN 'quarter' THEN l.period_months = 3
        ELSE true
    END
    AND (l.value IS NOT NULL OR d.when_missing = 'null');

-- Lineage: metric -> the facts it used, one row per input line (an account a document prints on two lines has two
-- rows). fact_id is NULL when an input is missing (which is why the metric is NULL), or for an input that does not
-- apply to this company (a lender metric lists both the NBFC and the bank lines) or is not printed and counts as nil
-- (gross margin's purchases of stock-in-trade and changes in inventories). input_period is the period the
-- input comes from (metric_input_accounts.input_period). When a total that has stand-in lines is not reported, its
-- row has fact_id NULL and the reported lines summed in its place follow (rows with stands_in_for set appear only
-- then; a line held in another reported line, as cash in cash and bank balances, does not). EBITDA's PBT before exceptional items lists only the lines that period uses (metric_input_accounts
-- exceptional). stated_id is set (fact_id NULL, value the stated value) when the input is a figure the user stated
-- (current_facts.stated_id).
-- about: The facts each metric used, one row per input line; fact_id and stated_id both NULL means the input is missing.
-- example: SELECT metric, account, input_period, fact_id, value FROM metric_inputs WHERE metric = 'current_ratio' AND period = 'FY25'
CREATE VIEW metric_inputs AS
WITH wanted AS (
    SELECT
        m.fiscal_year,
        m.period,
        m.scope,
        m.metric,
        d.account,
        d.stands_in_for,
        d.exceptional,
        CASE d.input_period
            WHEN 'same' THEN m.period
            WHEN 'year-1' THEN prior_period(m.period)
            WHEN 'year-3' THEN fy_back(m.period, 3)
            WHEN 'year-5' THEN fy_back(m.period, 5)
            WHEN 'quarter-1' THEN previous_quarter(m.period)
            WHEN 'quarter-2' THEN previous_quarter(previous_quarter(m.period))
            WHEN 'quarter-3' THEN previous_quarter(previous_quarter(previous_quarter(m.period)))
        END AS input_period
    FROM metrics m
    JOIN metric_input_accounts d ON d.metric = m.metric
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
    coalesce(line.value, CASE WHEN f.stated_id IS NOT NULL THEN f.value END) AS value,
    f.stated_id
FROM wanted w
LEFT JOIN current_facts f ON f.scope = w.scope AND f.account = w.account AND f.period = w.input_period
LEFT JOIN facts line ON list_contains(f.fact_ids, line.fact_id)
WHERE (
        w.stands_in_for IS NULL
        OR (
            (f.fact_id IS NOT NULL OR f.stated_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM current_facts t
                LEFT JOIN stand_ins s ON s.total = w.stands_in_for AND s.account = w.account
                WHERE t.account IN (w.stands_in_for, s.part_of) AND t.scope = w.scope AND t.period = w.period
            )
        )
    )
    AND (
        w.exceptional IS NULL
        OR w.exceptional = EXISTS (
            SELECT 1 FROM current_facts t
            WHERE t.account IN ('exceptional_items', 'extraordinary_items') AND t.value <> 0
                AND t.scope = w.scope AND t.period = w.input_period
        )
    );
