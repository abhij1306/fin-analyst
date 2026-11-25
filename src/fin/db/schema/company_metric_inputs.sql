-- Part of the company schema. Runs after company_metric_definitions.sql.

-- Which accounts each metric uses, for metric_inputs. Keep it in step with the formulas in metrics_wide.
-- input_period: which period the input comes from, relative to the metric's: 'same', 'year-1' (the same period a
-- year before), 'year-3', 'year-5', 'quarter-1' (the quarter before), 'quarter-2', 'quarter-3'.
-- stands_in_for: when the document does not print that total (stand_ins; and a bank's total equity), the lines
-- summed in its place. These rows appear in metric_inputs only then.
-- exceptional: for the EBITDA input "PBT before exceptional items" (conventions): false for pbt, which is used only
-- when the period has no exceptional or extraordinary items; true for profit_before_exceptional_items and
-- share_of_associates_jv, used only when it has. NULL for every other input (always used).
CREATE VIEW metric_input_accounts AS
WITH ebitda(a) AS (SELECT ['pbt_before_exceptional', 'finance_cost', 'depreciation', 'other_income']),
debt(a) AS (SELECT ['lt_borrowings', 'st_borrowings']),
ttm(a) AS (SELECT ['same', 'quarter-1', 'quarter-2', 'quarter-3']),
lender(a) AS (SELECT ['interest_income', 'revenue', 'interest_expended', 'finance_cost']),
charge(a) AS (SELECT ['impairment_on_financial_instruments', 'provisions_and_contingencies']),
inputs(metric, input_periods, accounts) AS (
    SELECT * FROM (VALUES
        ('revenue', ['same'], ['revenue']),
        ('pat', ['same'], ['pat']),
        ('gross_margin', ['same'], ['revenue', 'cost_of_materials', 'purchases_stock_in_trade',
            'change_in_inventories']),
        ('ebitda', ['same'], (FROM ebitda)),
        ('ebitda_margin', ['same'], list_concat((FROM ebitda), ['revenue'])),
        ('ebit', ['same'], ['pbt', 'finance_cost']),
        ('ebit_margin', ['same'], ['pbt', 'finance_cost', 'revenue']),
        ('pbt_margin', ['same'], ['pbt', 'revenue']),
        ('pat_margin', ['same'], ['pat', 'revenue']),
        ('roe', ['same'], ['pat', 'total_equity']),
        ('roce', ['same'], list_concat((FROM debt), ['pbt', 'finance_cost', 'total_equity'])),
        ('roa', ['same'], ['pat', 'total_assets']),
        ('roe_avg', ['same'], ['pat']),
        ('roe_avg', ['same', 'year-1'], ['total_equity']),
        ('roce_avg', ['same'], ['pbt', 'finance_cost']),
        ('roce_avg', ['same', 'year-1'], list_concat((FROM debt), ['total_equity'])),
        ('roa_avg', ['same'], ['pat']),
        ('roa_avg', ['same', 'year-1'], ['total_assets']),
        ('current_ratio', ['same'], ['total_current_assets', 'total_current_liabilities']),
        ('quick_ratio', ['same'], ['total_current_assets', 'inventories', 'total_current_liabilities']),
        ('cash_ratio', ['same'], ['cash', 'total_current_liabilities']),
        ('debt_to_equity', ['same'], list_concat((FROM debt), ['total_equity'])),
        ('debt_to_ebitda', ['same'], list_concat((FROM debt), (FROM ebitda))),
        ('net_debt', ['same'], list_concat((FROM debt), ['cash'])),
        ('net_debt_to_ebitda', ['same'], list_concat((FROM debt), ['cash'], (FROM ebitda))),
        ('interest_cover', ['same'], (FROM ebitda)),
        ('debt_to_assets', ['same'], list_concat((FROM debt), ['total_assets'])),
        ('asset_turnover', ['same'], ['revenue', 'total_assets']),
        ('fixed_asset_turnover', ['same'], ['revenue', 'ppe_net']),
        ('receivable_days', ['same'], ['trade_receivables', 'revenue']),
        ('inventory_days', ['same'], ['inventories', 'cost_of_materials']),
        ('payable_days', ['same'], ['trade_payables', 'cost_of_materials']),
        ('cash_conversion_cycle', ['same'], ['trade_receivables', 'revenue', 'inventories', 'trade_payables',
            'cost_of_materials']),
        ('cfo', ['same'], ['cfo']),
        ('free_cash_flow', ['same'], ['cfo', 'cf_capex']),
        ('cfo_to_ebitda', ['same'], list_concat((FROM ebitda), ['cfo'])),
        ('cfo_to_pat', ['same'], ['cfo', 'pat']),
        ('capex_to_revenue', ['same'], ['cf_capex', 'revenue']),
        ('capex_to_depreciation', ['same'], ['cf_capex', 'depreciation']),
        ('revenue_growth', ['same', 'year-1'], ['revenue']),
        ('revenue_growth_qoq', ['same', 'quarter-1'], ['revenue']),
        ('ebitda_growth', ['same', 'year-1'], (FROM ebitda)),
        ('pat_growth', ['same', 'year-1'], ['pat']),
        ('total_assets_growth', ['same', 'year-1'], ['total_assets']),
        ('total_equity_growth', ['same', 'year-1'], ['total_equity']),
        ('other_income_to_pbt', ['same'], ['other_income', 'pbt']),
        ('exceptional_items_to_pbt', ['same'], ['exceptional_items', 'pbt']),
        ('effective_tax_rate', ['same'], ['total_tax_expense', 'pbt']),
        ('eps_basic', ['same'], ['eps_basic']),
        ('eps_diluted', ['same'], ['eps_diluted']),
        ('net_interest_income', ['same'], (FROM lender)),
        ('cost_to_income', ['same'], list_concat((FROM lender), (FROM charge),
            ['total_income', 'total_expenses'])),
        ('credit_cost', ['same'], list_concat(['interest_income', 'interest_expended'], (FROM charge))),
        ('credit_cost', ['same', 'year-1'], ['loans']),
        ('assets_to_equity', ['same'], ['interest_income', 'interest_expended', 'total_assets', 'total_equity']),
        ('ttm_revenue', (FROM ttm), ['revenue']),
        ('ttm_ebitda', (FROM ttm), (FROM ebitda)),
        ('ttm_pat', (FROM ttm), ['pat']),
        ('ttm_ebitda_margin', (FROM ttm), list_concat((FROM ebitda), ['revenue'])),
        ('ttm_pat_margin', (FROM ttm), ['pat', 'revenue'])
    )
    UNION ALL
    SELECT metric, ['same', 'year-' || n], CASE account WHEN 'ebitda' THEN (FROM ebitda) ELSE [account] END
    FROM cagr_metrics
),
by_period AS (SELECT metric, unnest(input_periods) AS input_period, accounts FROM inputs),
by_account AS (SELECT metric, unnest(accounts) AS account, input_period FROM by_period),
pbt_before_exceptional(account, exceptional) AS (
    VALUES ('pbt', false), ('profit_before_exceptional_items', true), ('share_of_associates_jv', true)
)
SELECT a.metric, coalesce(x.account, a.account) AS account, a.input_period, NULL::VARCHAR AS stands_in_for,
    x.exceptional
FROM by_account a
LEFT JOIN pbt_before_exceptional x ON a.account = 'pbt_before_exceptional'
UNION ALL
SELECT a.metric, s.account, a.input_period, s.total, NULL
FROM by_account a
JOIN stand_ins s ON s.total = a.account
UNION ALL
-- A bank prints no total equity: assets_to_equity (lenders only) uses capital plus reserves instead.
SELECT 'assets_to_equity', unnest(['share_capital', 'other_equity']), 'same', 'total_equity', NULL;
