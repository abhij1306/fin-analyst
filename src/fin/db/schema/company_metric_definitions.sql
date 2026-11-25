-- Part of the company schema (see company_views.sql for the rules at the top). Runs after company_views.sql.

-- One row per metric: what it is, for people and for Claude. category groups metrics in `/ask`, the Excel export
-- and the dashboard. periods: 'annual' (fiscal years only), 'all' (years, quarters, half years and nine months),
-- 'interim' (every period but years: for a year the figure is a fact), 'quarter' (quarters only). when_missing:
-- see the rules at the top. label: the display name (report tables, charts). times: a ratio shown as times ("2.35x")
-- instead of a percentage. position: display order (the order of the rows below; CAGRs come after them).
CREATE VIEW metric_definitions AS
SELECT row_number() OVER () AS position, * FROM (VALUES
    -- headline figures of an interim period (for a year they are facts: revenue, pat)
    ('revenue', 'headline', 'rupees', 'interim', 'null', 'Revenue', false,
        'Revenue from operations of the period.', 'revenue'),
    ('pat', 'headline', 'rupees', 'interim', 'null', 'Profit after tax', false,
        'Profit after tax of the period.', 'pat'),
    -- profitability
    ('gross_margin', 'profitability', 'ratio', 'all', 'null', 'Gross margin', false,
        'Revenue left after the cost of goods sold, as a fraction of revenue: cost of materials consumed, plus '
        || 'purchases of stock-in-trade and changes in inventories when printed (counted as nil when not). No value '
        || 'without cost of materials consumed.',
        '(revenue - cost_of_materials - purchases_stock_in_trade - change_in_inventories) / revenue'),
    ('ebitda', 'profitability', 'rupees', 'all', 'null', 'EBITDA', false,
        'Operating profit before depreciation, interest and tax, without other income and without exceptional or '
        || 'extraordinary items.',
        'pbt before exceptional items + finance_cost + depreciation - other_income; pbt before exceptional items is '
        || 'profit_before_exceptional_items + share_of_associates_jv when the period has exceptional or '
        || 'extraordinary items, else pbt'),
    ('ebitda_margin', 'profitability', 'ratio', 'all', 'null', 'EBITDA margin', false,
        'EBITDA as a fraction of revenue.', 'ebitda / revenue'),
    ('ebit', 'profitability', 'rupees', 'all', 'null', 'EBIT', false,
        'Profit before interest and tax (other income included), as used by ROCE.', 'pbt + finance_cost'),
    ('ebit_margin', 'profitability', 'ratio', 'all', 'null', 'EBIT margin', false,
        'EBIT as a fraction of revenue.', 'ebit / revenue'),
    ('pbt_margin', 'profitability', 'ratio', 'all', 'null', 'PBT margin', false,
        'Profit before tax as a fraction of revenue.', 'pbt / revenue'),
    ('pat_margin', 'profitability', 'ratio', 'all', 'null', 'PAT margin', false,
        'Profit after tax as a fraction of revenue.', 'pat / revenue'),
    -- returns
    ('roe', 'returns', 'ratio', 'annual', 'null', 'ROE', false,
        'Return on equity on the closing balance sheet.', 'pat / total_equity'),
    ('roce', 'returns', 'ratio', 'annual', 'null', 'ROCE', false,
        'Return on capital employed (equity plus debt) on the closing balance sheet.',
        'ebit / (total_equity + debt)'),
    ('roa', 'returns', 'ratio', 'annual', 'null', 'ROA', false,
        'Return on assets on the closing balance sheet.', 'pat / total_assets'),
    ('roe_avg', 'returns', 'ratio', 'annual', 'null', 'ROE (average equity)', false,
        'Return on equity on the average of opening and closing equity (needs the previous year''s balance sheet).',
        'pat / ((total_equity + total_equity a year before) / 2)'),
    ('roce_avg', 'returns', 'ratio', 'annual', 'null', 'ROCE (average capital)', false,
        'Return on capital employed on the average of opening and closing equity plus debt (needs the previous '
        || 'year''s balance sheet).',
        'ebit / ((total_equity + debt + the same a year before) / 2)'),
    ('roa_avg', 'returns', 'ratio', 'annual', 'null', 'ROA (average assets)', false,
        'Return on assets on the average of opening and closing total assets (needs the previous year''s balance '
        || 'sheet).',
        'pat / ((total_assets + total_assets a year before) / 2)'),
    -- liquidity
    ('current_ratio', 'liquidity', 'ratio', 'annual', 'null', 'Current ratio', true,
        'Current assets over current liabilities. When a total is not printed, the current lines printed are '
        || 'summed.', 'total_current_assets / total_current_liabilities'),
    ('quick_ratio', 'liquidity', 'ratio', 'annual', 'null', 'Quick ratio', true,
        'Current assets other than inventories over current liabilities.',
        '(total_current_assets - inventories) / total_current_liabilities'),
    ('cash_ratio', 'liquidity', 'ratio', 'annual', 'null', 'Cash ratio', true,
        'Cash and cash equivalents over current liabilities. When cash and cash equivalents are not printed, '
        || 'cash and bank balances (old format, which also holds bank deposits) stand in.',
        'cash / total_current_liabilities'),
    -- solvency
    ('debt_to_equity', 'solvency', 'ratio', 'annual', 'null', 'Debt / equity', true,
        'Borrowings over equity.', 'debt / total_equity'),
    ('debt_to_ebitda', 'solvency', 'ratio', 'annual', 'null', 'Debt / EBITDA', true,
        'Borrowings over a year''s EBITDA.', 'debt / ebitda'),
    ('net_debt', 'solvency', 'rupees', 'annual', 'null', 'Net debt', false,
        'Borrowings less cash and cash equivalents (negative when cash is larger). When cash and cash equivalents '
        || 'are not printed, cash and bank balances (old format, which also holds bank deposits) stand in.',
        'debt - cash'),
    ('net_debt_to_ebitda', 'solvency', 'ratio', 'annual', 'null', 'Net debt / EBITDA', true,
        'Net debt over a year''s EBITDA.', 'net_debt / ebitda'),
    ('interest_cover', 'solvency', 'ratio', 'annual', 'null', 'Interest cover', true,
        'How many times EBITDA covers finance costs.', 'ebitda / finance_cost'),
    ('debt_to_assets', 'solvency', 'ratio', 'annual', 'null', 'Debt / assets', false,
        'Borrowings as a fraction of total assets.', 'debt / total_assets'),
    -- efficiency
    ('asset_turnover', 'efficiency', 'ratio', 'annual', 'null', 'Asset turnover', true,
        'Revenue per rupee of total assets (closing).', 'revenue / total_assets'),
    ('fixed_asset_turnover', 'efficiency', 'ratio', 'annual', 'null', 'Fixed asset turnover', true,
        'Revenue per rupee of property, plant and equipment, net block (closing).', 'revenue / ppe_net'),
    ('receivable_days', 'efficiency', 'days', 'annual', 'null', 'Receivable days', false,
        'Days of revenue held as trade receivables (closing, 365-day year).',
        'trade_receivables * 365 / revenue'),
    ('inventory_days', 'efficiency', 'days', 'annual', 'null', 'Inventory days', false,
        'Days of cost of materials consumed held as inventories (closing, 365-day year).',
        'inventories * 365 / cost_of_materials'),
    ('payable_days', 'efficiency', 'days', 'annual', 'null', 'Payable days', false,
        'Days of cost of materials consumed owed as trade payables (closing, 365-day year).',
        'trade_payables * 365 / cost_of_materials'),
    ('cash_conversion_cycle', 'efficiency', 'days', 'annual', 'null', 'Cash conversion cycle', false,
        'Receivable days plus inventory days less payable days.',
        'receivable_days + inventory_days - payable_days'),
    -- cash flow
    ('cfo', 'cash_flow', 'rupees', 'annual', 'null', 'Cash from operations', false,
        'Net cash from operating activities.', 'cfo'),
    ('free_cash_flow', 'cash_flow', 'rupees', 'annual', 'null', 'Free cash flow', false,
        'Cash from operations less purchase of property and equipment.', 'cfo + cf_capex (capex is negative)'),
    ('cfo_to_ebitda', 'cash_flow', 'ratio', 'annual', 'null', 'CFO / EBITDA', true,
        'How much of EBITDA came in as operating cash.', 'cfo / ebitda'),
    ('cfo_to_pat', 'cash_flow', 'ratio', 'annual', 'null', 'CFO / PAT', true,
        'How much of profit after tax came in as operating cash.', 'cfo / pat'),
    ('capex_to_revenue', 'cash_flow', 'ratio', 'annual', 'null', 'Capex / revenue', false,
        'Purchase of property and equipment as a fraction of revenue.', '-cf_capex / revenue'),
    ('capex_to_depreciation', 'cash_flow', 'ratio', 'annual', 'null', 'Capex / depreciation', true,
        'Purchase of property and equipment over depreciation.', '-cf_capex / depreciation'),
    -- growth: year on year against the same period a year before; NULL unless the earlier value is positive
    ('revenue_growth', 'growth', 'ratio', 'all', 'null', 'Revenue growth', false,
        'Revenue growth against the same period a year before.', 'revenue / revenue a year before - 1'),
    ('revenue_growth_qoq', 'growth', 'ratio', 'quarter', 'null', 'Revenue growth on the previous quarter', false,
        'Revenue growth against the quarter before (Q1 against the previous year''s Q4).',
        'revenue / revenue of the quarter before - 1'),
    ('ebitda_growth', 'growth', 'ratio', 'all', 'null', 'EBITDA growth', false,
        'EBITDA growth against the same period a year before.', 'ebitda / ebitda a year before - 1'),
    ('pat_growth', 'growth', 'ratio', 'all', 'null', 'PAT growth', false,
        'Profit after tax growth against the same period a year before.', 'pat / pat a year before - 1'),
    ('total_assets_growth', 'growth', 'ratio', 'annual', 'null', 'Total assets growth', false,
        'Growth of total assets over the year.', 'total_assets / total_assets a year before - 1'),
    ('total_equity_growth', 'growth', 'ratio', 'annual', 'null', 'Total equity growth', false,
        'Growth of total equity over the year.', 'total_equity / total_equity a year before - 1'),
    -- earnings quality
    ('other_income_to_pbt', 'earnings_quality', 'ratio', 'all', 'null', 'Other income / PBT', false,
        'Share of profit before tax that is other income.', 'other_income / pbt'),
    ('exceptional_items_to_pbt', 'earnings_quality', 'ratio', 'all', 'absent', 'Exceptional items / PBT', false,
        'Exceptional items as printed (Schedule III prints a charge as positive) over profit before tax; only '
        || 'when the statement shows exceptional items.', 'exceptional_items / pbt'),
    ('effective_tax_rate', 'earnings_quality', 'ratio', 'all', 'null', 'Effective tax rate', false,
        'Total tax expense over profit before tax. When total tax is not printed, the tax lines printed are '
        || 'summed.', 'total_tax_expense / pbt'),
    -- per share, as printed
    ('eps_basic', 'per_share', 'rupees_per_share', 'all', 'absent', 'EPS, basic', false,
        'Basic earnings per share as printed.', 'eps_basic'),
    ('eps_diluted', 'per_share', 'rupees_per_share', 'all', 'absent', 'EPS, diluted', false,
        'Diluted earnings per share as printed.', 'eps_diluted'),
    -- lenders (NBFCs and banks): only when the statement has interest income (NBFC) or interest expended (bank)
    ('net_interest_income', 'lender', 'rupees', 'all', 'absent', 'Net interest income', false,
        'Interest income less interest expense. Interest income is the NBFC line, or a bank''s interest earned '
        || '(revenue); interest expense is a bank''s interest expended, else finance costs.',
        'interest_income - interest_expense'),
    ('cost_to_income', 'lender', 'ratio', 'all', 'absent', 'Cost to income', false,
        'Operating expenses over net total income: total expenses less interest expense and impairment or '
        || 'provisions, over total income less interest expense. No value when neither impairment nor provisions '
        || 'is printed.',
        '(total_expenses - interest_expense - impairment or provisions) / (total_income - interest_expense)'),
    ('credit_cost', 'lender', 'ratio', 'annual', 'absent', 'Credit cost', false,
        'Impairment on financial instruments (NBFC) or provisions and contingencies (bank) over average loans '
        || 'or advances (needs the previous year''s balance sheet).',
        'impairment or provisions / ((loans + loans a year before) / 2)'),
    ('assets_to_equity', 'lender', 'ratio', 'annual', 'absent', 'Assets / equity', true,
        'Total assets over equity (leverage). When total equity is not printed (banks), capital plus reserves.',
        'total_assets / total_equity'),
    -- trailing twelve months: the quarter and the three before it, all loaded
    ('ttm_revenue', 'ttm', 'rupees', 'quarter', 'absent', 'Revenue, last four quarters', false,
        'Revenue of the last four quarters.', 'revenue of this quarter and the three before'),
    ('ttm_ebitda', 'ttm', 'rupees', 'quarter', 'absent', 'EBITDA, last four quarters', false,
        'EBITDA of the last four quarters.', 'ebitda of this quarter and the three before'),
    ('ttm_pat', 'ttm', 'rupees', 'quarter', 'absent', 'PAT, last four quarters', false,
        'Profit after tax of the last four quarters.', 'pat of this quarter and the three before'),
    ('ttm_ebitda_margin', 'ttm', 'ratio', 'quarter', 'absent', 'EBITDA margin, last four quarters', false,
        'EBITDA margin on the last four quarters.', 'ttm_ebitda / ttm_revenue'),
    ('ttm_pat_margin', 'ttm', 'ratio', 'quarter', 'absent', 'PAT margin, last four quarters', false,
        'Profit after tax margin on the last four quarters.', 'ttm_pat / ttm_revenue')
) AS t(metric, category, unit, periods, when_missing, label, times, definition, formula)
UNION ALL
-- 3- and 5-year compound annual growth, only when both ends are loaded and positive
SELECT
    position, metric, 'growth', 'ratio', 'annual', 'absent', name || ' CAGR, ' || n || ' years', false,
    'Compound annual growth of ' || what || ' over ' || n || ' years (both ends loaded and positive).',
    '(' || account || ' / ' || account || ' ' || n || ' years before) ^ (1/' || n || ') - 1'
FROM cagr_metrics;
