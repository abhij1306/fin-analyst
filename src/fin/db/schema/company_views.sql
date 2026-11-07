-- Metrics (PLAN.md Phase 7): macros and plain views over facts, so they are always current and never stored twice.
-- THIS FILE IS WHERE A METRIC IS ADDED OR CHANGED (docs/CUSTOMISING.md "Add a metric"):
--   1. a row in `metric_definitions` (category, unit, which periods, plain-English definition, formula);
--   2. its input accounts in `metric_input_accounts` (for lineage, `metric_inputs`);
--   3. its formula in the `wide` step of the `metrics` view, and its name in the UNPIVOT list there.
--
-- Inputs: current_facts holds the facts a metric may use (see below). Queries should use current_facts too.
--
-- Definitions (tests/fixtures/sample-financials/answer_key.json "conventions"):
-- - Balance sheet items are closing balances of the year; days use a 365-day year. Metrics named *_avg use the
--   average of the opening (previous year's closing) and closing balance instead.
-- - EBITDA = PBT before exceptional items + finance cost + depreciation - other income, so a one-off exceptional
--   or extraordinary item does not move EBITDA, its margin, debt/EBITDA or its growth. PBT before exceptional items
--   is PBT when the period has no exceptional or extraordinary items (none printed, or printed as nil); otherwise
--   the printed "Profit before exceptional items and tax" plus any share of associates and joint ventures (which
--   PBT includes). Exceptional items are stored as printed, with either sign, so their sign is never guessed.
--   EBIT = PBT + finance cost.
-- - Debt = long-term borrowings + short-term borrowings. Net debt = debt - cash and cash equivalents.
-- - Inventory days and payable days are on cost of materials consumed.
-- - Cash flow outflows are negative, so capex (cf_capex) is negative.
-- - Each metric is per period and scope. Standalone and consolidated are never mixed.
--
-- Rules:
-- - A metric whose inputs are missing, or whose denominator is zero, has no value. Nothing is guessed and nothing
--   is zero. metric_definitions.when_missing says how: 'null' keeps the row with value NULL (so the report can say
--   which input is missing); 'absent' leaves the row out (metrics that only some companies or periods have:
--   lender metrics, per-share figures, multi-year growth, trailing twelve months, exceptional items).
-- - Money metrics (ebitda, free_cash_flow, ...) are exact sums in rupees; eps_* are rupees per share as printed.
-- - Ratios and days are computed exactly on integer paise and rounded once, half away from zero, to 6
--   decimal places (div6 below). DuckDB divides DECIMALs as DOUBLE, so div6 uses integer arithmetic instead.
--   A ratio is a fraction: 0.180000 means 18%. CAGRs need a root, so they are computed in DOUBLE and rounded to 6.
-- - metrics.value is DECIMAL(38, 6) for every metric; metrics.unit is rupees, rupees_per_share, ratio or days.

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

-- The fiscal year n years before a period's: fy_back('FY25', 3) -> 'FY22'.
CREATE MACRO fy_back(p, n) AS printf('FY%02d', (CAST(substr(split_part(p, '-', 1), 3) AS INTEGER) + 100 - n) % 100);

-- Growth x / earlier - 1, rounded like div6; NULL unless the earlier value is positive.
CREATE MACRO growth6(x, earlier) AS CASE WHEN earlier > 0 THEN div6(x - earlier, earlier) END;

-- Compound annual growth over n years, (x / earlier) ^ (1 / n) - 1, in DOUBLE rounded to 6 places; NULL unless both
-- values are positive.
CREATE MACRO cagr6(x, earlier, n) AS
    CASE WHEN x > 0 AND earlier > 0 THEN CAST(round(pow(x / earlier, 1.0 / n) - 1, 6) AS DECIMAL(38, 6)) END;

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

-- One row per metric: what it is, for people and for Claude. category groups metrics in `/ask`, the Excel export
-- and the dashboard. periods: 'annual' (fiscal years only), 'all' (years, quarters, half years and nine months),
-- 'interim' (every period but years: for a year the figure is a fact), 'quarter' (quarters only). when_missing:
-- see the rules at the top.
CREATE VIEW metric_definitions AS
SELECT * FROM (VALUES
    -- headline figures of an interim period (for a year they are facts: revenue, pat)
    ('revenue', 'headline', 'rupees', 'interim', 'null',
        'Revenue from operations of the period.', 'revenue'),
    ('pat', 'headline', 'rupees', 'interim', 'null',
        'Profit after tax of the period.', 'pat'),
    -- profitability
    ('gross_margin', 'profitability', 'ratio', 'all', 'null',
        'Revenue left after cost of materials consumed, as a fraction of revenue.',
        '(revenue - cost_of_materials) / revenue'),
    ('ebitda', 'profitability', 'rupees', 'all', 'null',
        'Operating profit before depreciation, interest and tax, without other income and without exceptional or '
        || 'extraordinary items.',
        'pbt before exceptional items + finance_cost + depreciation - other_income; pbt before exceptional items is '
        || 'profit_before_exceptional_items + share_of_associates_jv when the period has exceptional or '
        || 'extraordinary items, else pbt'),
    ('ebitda_margin', 'profitability', 'ratio', 'all', 'null',
        'EBITDA as a fraction of revenue.', 'ebitda / revenue'),
    ('ebit', 'profitability', 'rupees', 'all', 'null',
        'Profit before interest and tax (other income included), as used by ROCE.', 'pbt + finance_cost'),
    ('ebit_margin', 'profitability', 'ratio', 'all', 'null',
        'EBIT as a fraction of revenue.', 'ebit / revenue'),
    ('pbt_margin', 'profitability', 'ratio', 'all', 'null',
        'Profit before tax as a fraction of revenue.', 'pbt / revenue'),
    ('pat_margin', 'profitability', 'ratio', 'all', 'null',
        'Profit after tax as a fraction of revenue.', 'pat / revenue'),
    -- returns
    ('roe', 'returns', 'ratio', 'annual', 'null',
        'Return on equity on the closing balance sheet.', 'pat / total_equity'),
    ('roce', 'returns', 'ratio', 'annual', 'null',
        'Return on capital employed (equity plus debt) on the closing balance sheet.',
        'ebit / (total_equity + debt)'),
    ('roa', 'returns', 'ratio', 'annual', 'null',
        'Return on assets on the closing balance sheet.', 'pat / total_assets'),
    ('roe_avg', 'returns', 'ratio', 'annual', 'null',
        'Return on equity on the average of opening and closing equity (needs the previous year''s balance sheet).',
        'pat / ((total_equity + total_equity a year before) / 2)'),
    ('roce_avg', 'returns', 'ratio', 'annual', 'null',
        'Return on capital employed on the average of opening and closing equity plus debt (needs the previous '
        || 'year''s balance sheet).',
        'ebit / ((total_equity + debt + the same a year before) / 2)'),
    ('roa_avg', 'returns', 'ratio', 'annual', 'null',
        'Return on assets on the average of opening and closing total assets (needs the previous year''s balance '
        || 'sheet).',
        'pat / ((total_assets + total_assets a year before) / 2)'),
    -- liquidity
    ('current_ratio', 'liquidity', 'ratio', 'annual', 'null',
        'Current assets over current liabilities. When a total is not printed, the current lines printed are '
        || 'summed.', 'total_current_assets / total_current_liabilities'),
    ('quick_ratio', 'liquidity', 'ratio', 'annual', 'null',
        'Current assets other than inventories over current liabilities.',
        '(total_current_assets - inventories) / total_current_liabilities'),
    ('cash_ratio', 'liquidity', 'ratio', 'annual', 'null',
        'Cash and cash equivalents over current liabilities.', 'cash / total_current_liabilities'),
    -- solvency
    ('debt_to_equity', 'solvency', 'ratio', 'annual', 'null',
        'Borrowings over equity.', 'debt / total_equity'),
    ('debt_to_ebitda', 'solvency', 'ratio', 'annual', 'null',
        'Borrowings over a year''s EBITDA.', 'debt / ebitda'),
    ('net_debt', 'solvency', 'rupees', 'annual', 'null',
        'Borrowings less cash and cash equivalents (negative when cash is larger).', 'debt - cash'),
    ('net_debt_to_ebitda', 'solvency', 'ratio', 'annual', 'null',
        'Net debt over a year''s EBITDA.', 'net_debt / ebitda'),
    ('interest_cover', 'solvency', 'ratio', 'annual', 'null',
        'How many times EBITDA covers finance costs.', 'ebitda / finance_cost'),
    ('debt_to_assets', 'solvency', 'ratio', 'annual', 'null',
        'Borrowings as a fraction of total assets.', 'debt / total_assets'),
    -- efficiency
    ('asset_turnover', 'efficiency', 'ratio', 'annual', 'null',
        'Revenue per rupee of total assets (closing).', 'revenue / total_assets'),
    ('fixed_asset_turnover', 'efficiency', 'ratio', 'annual', 'null',
        'Revenue per rupee of property, plant and equipment, net block (closing).', 'revenue / ppe_net'),
    ('receivable_days', 'efficiency', 'days', 'annual', 'null',
        'Days of revenue held as trade receivables (closing, 365-day year).',
        'trade_receivables * 365 / revenue'),
    ('inventory_days', 'efficiency', 'days', 'annual', 'null',
        'Days of cost of materials consumed held as inventories (closing, 365-day year).',
        'inventories * 365 / cost_of_materials'),
    ('payable_days', 'efficiency', 'days', 'annual', 'null',
        'Days of cost of materials consumed owed as trade payables (closing, 365-day year).',
        'trade_payables * 365 / cost_of_materials'),
    ('cash_conversion_cycle', 'efficiency', 'days', 'annual', 'null',
        'Receivable days plus inventory days less payable days.',
        'receivable_days + inventory_days - payable_days'),
    -- cash flow
    ('cfo', 'cash_flow', 'rupees', 'annual', 'null',
        'Net cash from operating activities.', 'cfo'),
    ('free_cash_flow', 'cash_flow', 'rupees', 'annual', 'null',
        'Cash from operations less purchase of property and equipment.', 'cfo + cf_capex (capex is negative)'),
    ('cfo_to_ebitda', 'cash_flow', 'ratio', 'annual', 'null',
        'How much of EBITDA came in as operating cash.', 'cfo / ebitda'),
    ('cfo_to_pat', 'cash_flow', 'ratio', 'annual', 'null',
        'How much of profit after tax came in as operating cash.', 'cfo / pat'),
    ('capex_to_revenue', 'cash_flow', 'ratio', 'annual', 'null',
        'Purchase of property and equipment as a fraction of revenue.', '-cf_capex / revenue'),
    ('capex_to_depreciation', 'cash_flow', 'ratio', 'annual', 'null',
        'Purchase of property and equipment over depreciation.', '-cf_capex / depreciation'),
    -- growth: year on year against the same period a year before; NULL unless the earlier value is positive
    ('revenue_growth', 'growth', 'ratio', 'all', 'null',
        'Revenue growth against the same period a year before.', 'revenue / revenue a year before - 1'),
    ('revenue_growth_qoq', 'growth', 'ratio', 'quarter', 'null',
        'Revenue growth against the quarter before (Q1 against the previous year''s Q4).',
        'revenue / revenue of the quarter before - 1'),
    ('ebitda_growth', 'growth', 'ratio', 'all', 'null',
        'EBITDA growth against the same period a year before.', 'ebitda / ebitda a year before - 1'),
    ('pat_growth', 'growth', 'ratio', 'all', 'null',
        'Profit after tax growth against the same period a year before.', 'pat / pat a year before - 1'),
    ('total_assets_growth', 'growth', 'ratio', 'annual', 'null',
        'Growth of total assets over the year.', 'total_assets / total_assets a year before - 1'),
    ('total_equity_growth', 'growth', 'ratio', 'annual', 'null',
        'Growth of total equity over the year.', 'total_equity / total_equity a year before - 1'),
    -- earnings quality
    ('other_income_to_pbt', 'earnings_quality', 'ratio', 'all', 'null',
        'Share of profit before tax that is other income.', 'other_income / pbt'),
    ('exceptional_items_to_pbt', 'earnings_quality', 'ratio', 'all', 'absent',
        'Exceptional items as printed (Schedule III prints a charge as positive) over profit before tax; only '
        || 'when the statement shows exceptional items.', 'exceptional_items / pbt'),
    ('effective_tax_rate', 'earnings_quality', 'ratio', 'all', 'null',
        'Total tax expense over profit before tax. When total tax is not printed, the tax lines printed are '
        || 'summed.', 'total_tax_expense / pbt'),
    -- per share, as printed
    ('eps_basic', 'per_share', 'rupees_per_share', 'all', 'absent',
        'Basic earnings per share as printed.', 'eps_basic'),
    ('eps_diluted', 'per_share', 'rupees_per_share', 'all', 'absent',
        'Diluted earnings per share as printed.', 'eps_diluted'),
    -- lenders (NBFCs and banks): only when the statement has interest income (NBFC) or interest expended (bank)
    ('net_interest_income', 'lender', 'rupees', 'all', 'absent',
        'Interest income less interest expense. Interest income is the NBFC line, or a bank''s interest earned '
        || '(revenue); interest expense is a bank''s interest expended, else finance costs.',
        'interest_income - interest_expense'),
    ('cost_to_income', 'lender', 'ratio', 'all', 'absent',
        'Operating expenses over net total income: total expenses less interest expense and impairment or '
        || 'provisions, over total income less interest expense.',
        '(total_expenses - interest_expense - impairment or provisions) / (total_income - interest_expense)'),
    ('credit_cost', 'lender', 'ratio', 'annual', 'absent',
        'Impairment on financial instruments (NBFC) or provisions and contingencies (bank) over average loans '
        || 'or advances (needs the previous year''s balance sheet).',
        'impairment or provisions / ((loans + loans a year before) / 2)'),
    ('assets_to_equity', 'lender', 'ratio', 'annual', 'absent',
        'Total assets over equity (leverage). When total equity is not printed (banks), capital plus reserves.',
        'total_assets / total_equity'),
    -- trailing twelve months: the quarter and the three before it, all loaded
    ('ttm_revenue', 'ttm', 'rupees', 'quarter', 'absent',
        'Revenue of the last four quarters.', 'revenue of this quarter and the three before'),
    ('ttm_ebitda', 'ttm', 'rupees', 'quarter', 'absent',
        'EBITDA of the last four quarters.', 'ebitda of this quarter and the three before'),
    ('ttm_pat', 'ttm', 'rupees', 'quarter', 'absent',
        'Profit after tax of the last four quarters.', 'pat of this quarter and the three before'),
    ('ttm_ebitda_margin', 'ttm', 'ratio', 'quarter', 'absent',
        'EBITDA margin on the last four quarters.', 'ttm_ebitda / ttm_revenue'),
    ('ttm_pat_margin', 'ttm', 'ratio', 'quarter', 'absent',
        'Profit after tax margin on the last four quarters.', 'ttm_pat / ttm_revenue')
) AS t(metric, category, unit, periods, when_missing, definition, formula)
UNION ALL
-- 3- and 5-year compound annual growth, only when both ends are loaded and positive
SELECT
    x || '_cagr_' || n || 'y', 'growth', 'ratio', 'annual', 'absent',
    'Compound annual growth of ' || what || ' over ' || n || ' years (both ends loaded and positive).',
    '(' || x || ' / ' || x || ' ' || n || ' years before) ^ (1/' || n || ') - 1'
FROM (VALUES ('revenue', 'revenue'), ('ebitda', 'EBITDA'), ('pat', 'profit after tax'),
    ('total_assets', 'total assets'), ('total_equity', 'total equity')) AS g(x, what),
    (VALUES (3), (5)) AS y(n);

-- Which accounts each metric uses, for metric_inputs. Keep it in step with the formulas in the metrics view.
-- input_period: which period the input comes from, relative to the metric's: 'same', 'year-1' (the same period a
-- year before), 'year-3', 'year-5', 'quarter-1' (the quarter before), 'quarter-2', 'quarter-3'.
-- stands_in_for: when the document does not print that total (total current assets or liabilities, total tax
-- expense, a bank's total equity), the lines summed in its place. These rows appear in metric_inputs only then.
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
        ('gross_margin', ['same'], ['revenue', 'cost_of_materials']),
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
        ('revenue_cagr_3y', ['same', 'year-3'], ['revenue']),
        ('revenue_cagr_5y', ['same', 'year-5'], ['revenue']),
        ('ebitda_cagr_3y', ['same', 'year-3'], (FROM ebitda)),
        ('ebitda_cagr_5y', ['same', 'year-5'], (FROM ebitda)),
        ('pat_cagr_3y', ['same', 'year-3'], ['pat']),
        ('pat_cagr_5y', ['same', 'year-5'], ['pat']),
        ('total_assets_cagr_3y', ['same', 'year-3'], ['total_assets']),
        ('total_assets_cagr_5y', ['same', 'year-5'], ['total_assets']),
        ('total_equity_cagr_3y', ['same', 'year-3'], ['total_equity']),
        ('total_equity_cagr_5y', ['same', 'year-5'], ['total_equity']),
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
SELECT metric, unnest(['inventories', 'current_investments', 'trade_receivables', 'cash', 'other_bank_balances',
    'other_current_assets']), 'same', 'total_current_assets', NULL
FROM (VALUES ('current_ratio'), ('quick_ratio')) AS m(metric)
UNION ALL
SELECT metric, unnest(['st_borrowings', 'lease_liabilities_current', 'trade_payables',
    'other_financial_liabilities_current', 'other_current_liabilities', 'st_provisions',
    'current_tax_liabilities']), 'same', 'total_current_liabilities', NULL
FROM (VALUES ('current_ratio'), ('quick_ratio'), ('cash_ratio')) AS m(metric)
UNION ALL
SELECT 'effective_tax_rate', unnest(['tax_expense', 'deferred_tax']), 'same', 'total_tax_expense', NULL
UNION ALL
SELECT 'assets_to_equity', unnest(['share_capital', 'other_equity']), 'same', 'total_equity', NULL;

-- Metrics, one row per period, scope and metric (long format). Which periods get a metric, and whether a metric
-- without a value is a NULL row or no row, is in metric_definitions (periods, when_missing).
--
-- Annual periods (period_months = 12) get the annual metrics. Quarter, half-year and nine-month periods get the
-- metrics that make sense on one period's flows ('all' and 'interim'; 'quarter' for quarters only). Ratios that
-- need a full year of flows or balance-sheet averages (days, returns, leverage, cash conversion) are annual: a
-- quarter's revenue against a closing balance is not comparable to the annual figure.
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
        max(p) FILTER (WHERE account = 'total_income') AS total_income,
        max(p) FILTER (WHERE account = 'total_expenses') AS total_expenses,
        max(p) FILTER (WHERE account = 'cost_of_materials') AS cost_of_materials,
        max(p) FILTER (WHERE account = 'finance_cost') AS finance_cost,
        max(p) FILTER (WHERE account = 'depreciation') AS depreciation,
        max(p) FILTER (WHERE account = 'profit_before_exceptional_items') AS profit_before_exceptional_items,
        max(p) FILTER (WHERE account = 'exceptional_items') AS exceptional_items,
        max(p) FILTER (WHERE account = 'extraordinary_items') AS extraordinary_items,
        max(p) FILTER (WHERE account = 'share_of_associates_jv') AS share_of_associates_jv,
        max(p) FILTER (WHERE account = 'pbt') AS pbt,
        -- The reported total tax, else the sum of the tax lines reported (see metric_input_accounts).
        coalesce(
            max(p) FILTER (WHERE account = 'total_tax_expense'),
            CAST(sum(p) FILTER (WHERE account IN ('tax_expense', 'deferred_tax')) AS HUGEINT)
        ) AS tax,
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
        -- The reported total, else the sum of the current liability lines reported (see metric_input_accounts).
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
),
wide AS (
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
        div6(revenue - cost_of_materials, revenue) AS gross_margin,
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
            total_expenses - interest_expense - coalesce(credit_charge, 0), total_income - interest_expense
        ) END AS cost_to_income,
        CASE WHEN lender THEN div6(2 * credit_charge, loans + prev_loans) END AS credit_cost,
        CASE WHEN lender THEN div6(total_assets, lender_equity) END AS assets_to_equity,
        -- trailing twelve months
        rupees(ttm_revenue) AS ttm_revenue,
        rupees(ttm_ebitda) AS ttm_ebitda,
        rupees(ttm_pat) AS ttm_pat,
        div6(ttm_ebitda, ttm_revenue) AS ttm_ebitda_margin,
        div6(ttm_pat, ttm_revenue) AS ttm_pat_margin
    FROM derived
),
long AS (
    FROM wide UNPIVOT INCLUDE NULLS (
        value FOR metric IN (
            revenue, pat, gross_margin, ebitda, ebitda_margin, ebit, ebit_margin, pbt_margin, pat_margin,
            roe, roce, roa, roe_avg, roce_avg, roa_avg, current_ratio, quick_ratio, cash_ratio,
            debt_to_equity, debt_to_ebitda, net_debt, net_debt_to_ebitda, interest_cover, debt_to_assets,
            asset_turnover, fixed_asset_turnover, receivable_days, inventory_days, payable_days,
            cash_conversion_cycle, cfo, free_cash_flow, cfo_to_ebitda, cfo_to_pat, capex_to_revenue,
            capex_to_depreciation, revenue_growth, revenue_growth_qoq, ebitda_growth, pat_growth,
            total_assets_growth, total_equity_growth, revenue_cagr_3y, revenue_cagr_5y, ebitda_cagr_3y,
            ebitda_cagr_5y, pat_cagr_3y, pat_cagr_5y, total_assets_cagr_3y, total_assets_cagr_5y,
            total_equity_cagr_3y, total_equity_cagr_5y, other_income_to_pbt, exceptional_items_to_pbt,
            effective_tax_rate, eps_basic, eps_diluted, net_interest_income, cost_to_income, credit_cost,
            assets_to_equity, ttm_revenue, ttm_ebitda, ttm_pat, ttm_ebitda_margin, ttm_pat_margin
        )
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
-- apply to this company (a lender metric lists both the NBFC and the bank lines). input_period is the period the
-- input comes from (metric_input_accounts.input_period). When a total that has stand-in lines is not reported, its
-- row has fact_id NULL and the reported lines summed in its place follow (rows with stands_in_for set appear only
-- then). EBITDA's PBT before exceptional items lists only the lines that period uses (metric_input_accounts
-- exceptional).
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
    line.value
FROM wanted w
LEFT JOIN current_facts f ON f.scope = w.scope AND f.account = w.account AND f.period = w.input_period
LEFT JOIN facts line ON list_contains(f.fact_ids, line.fact_id)
WHERE (
        w.stands_in_for IS NULL
        OR (
            f.fact_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM current_facts t
                WHERE t.account = w.stands_in_for AND t.scope = w.scope AND t.period = w.period
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
