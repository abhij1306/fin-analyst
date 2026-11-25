-- Metrics (docs/product.md): macros and plain views over facts, so they are always current and never stored twice.
-- THIS FILE IS WHERE A METRIC IS ADDED OR CHANGED (docs/development.md "Add a metric"):
--   1. a row in `metric_definitions` (category, unit, which periods, display label, plain-English definition,
--      formula);
--   2. its input accounts in `metric_input_accounts` (for lineage, `metric_inputs`);
--   3. its formula in the `metrics_wide` view (every column there is a metric).
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
-- - Debt = long-term borrowings + short-term borrowings. Net debt = debt - cash and cash equivalents. Cash is cash and
--   cash equivalents; when a period prints only old-format "Cash and bank balances", that line stands in (stand_ins),
--   and metric_inputs shows it.
-- - Inventory days and payable days are on cost of materials consumed. Gross margin is on the cost of goods sold:
--   cost of materials consumed + purchases of stock-in-trade + changes in inventories (the last two nil when not
--   printed; changes in inventories as printed, negative when inventories rose).
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
-- alone is used, so nothing is counted twice. With only two lines, equal values are as likely two equal parts with
-- no total printed, so one is the total only when the document says so: its label starts with "Total", or the other
-- line sits under it (raw_values.section is its label, list marker and colon aside); otherwise both are summed.
-- fact_ids lists the lines used; fact_id is the first of them.
CREATE VIEW document_facts AS
WITH lines AS (
    SELECT
        f.doc_id, f.account, f.fiscal_year, f.period, f.period_months, f.period_end, f.scope, f.fact_id, f.value,
        -- label and heading without a list marker ("(b)", "ii)", "b.") or a trailing colon
        regexp_replace(lower(trim(r.raw_label)), '^(\([a-z0-9]{1,4}\)|[a-z0-9]{1,4}[.)])\s*|\s*:$', '', 'g') AS label,
        regexp_replace(lower(trim(r.section)), '^(\([a-z0-9]{1,4}\)|[a-z0-9]{1,4}[.)])\s*|\s*:$', '', 'g') AS section,
        -- nil lines are left out of the total test: 500, 500 and a nil line are two lines, not a total and its parts
        sum(f.value) FILTER (WHERE f.value <> 0) OVER w AS lines_total,
        count(*) FILTER (WHERE f.value <> 0) OVER w AS line_count
    FROM facts f
    LEFT JOIN raw_values r ON r.raw_id = f.raw_id
    WHERE f.confirmed AND f.account IS NOT NULL
    WINDOW w AS (PARTITION BY f.doc_id, f.account, f.period, f.scope)
),
totals AS (
    SELECT t.doc_id, t.account, t.period, t.scope, min(t.fact_id) AS total_id
    FROM lines t
    WHERE t.value * 2 = t.lines_total
        AND (
            t.line_count > 2
            OR t.label LIKE 'total%'
            OR EXISTS (
                SELECT 1 FROM lines o
                WHERE o.doc_id = t.doc_id AND o.account = t.account AND o.period = t.period AND o.scope = t.scope
                    AND o.fact_id <> t.fact_id AND o.section = t.label
            )
        )
    GROUP BY t.doc_id, t.account, t.period, t.scope
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

-- How much of what the loaded documents print is mapped, per scope and statement (report "Data coverage", the
-- dashboard quality page). figures: values read (one per line and period); mapped: of those, confirmed to an
-- account (the rest are unmapped or waiting for review, and left out of every metric).
CREATE VIEW statement_coverage AS
SELECT
    f.scope,
    r.statement,
    count(*) AS figures,
    count(*) FILTER (WHERE f.confirmed) AS mapped,
    div6(count(*) FILTER (WHERE f.confirmed), count(*)) AS mapped_share
FROM facts f
JOIN raw_values r ON r.raw_id = f.raw_id
JOIN documents d ON d.doc_id = f.doc_id
WHERE d.status = 'loaded'
GROUP BY f.scope, r.statement;

-- The 3- and 5-year CAGR metrics, one row each: their definitions (metric_definitions) and inputs
-- (metric_input_accounts) are made from these rows. Their formulas are in metrics_wide.
CREATE VIEW cagr_metrics AS
SELECT x || '_cagr_' || n || 'y' AS metric, x AS account, what, name, n, 1000 + 2 * i + n // 5 AS position
FROM (VALUES ('revenue', 'revenue', 'Revenue', 1), ('ebitda', 'EBITDA', 'EBITDA', 2),
        ('pat', 'profit after tax', 'PAT', 3), ('total_assets', 'total assets', 'Total assets', 4),
        ('total_equity', 'total equity', 'Total equity', 5)) AS g(x, what, name, i),
    (VALUES (3), (5)) AS y(n);

-- Totals a document may not print, and the lines summed in their place when it does not: the metrics view uses the
-- sum (metrics_wide, its `paise` step), and metric_input_accounts lists the lines (stands_in_for) for every metric using the total.
-- The lines are the accounts whose parent is the total in templates/dictionary/schedule_iii.csv; cash (cash and cash
-- equivalents) is not a total, but old-format "Cash and bank balances" stands in for it. part_of: a line
-- printed that already holds this one; when the document prints it, this line is not added again (old-format
-- "Cash and bank balances" holds cash and cash equivalents and other bank balances).
CREATE VIEW stand_ins AS
SELECT total, account,
    CASE WHEN account IN ('cash', 'other_bank_balances') THEN 'cash_and_bank_balances' END AS part_of
FROM (
    SELECT total, unnest(accounts) AS account FROM (VALUES
        ('total_current_assets', ['inventories', 'current_investments', 'trade_receivables', 'cash',
            'other_bank_balances', 'cash_and_bank_balances', 'loans_current', 'other_financial_assets_current',
            'tax_assets_current', 'other_current_assets', 'assets_held_for_sale']),
        ('total_current_liabilities', ['st_borrowings', 'lease_liabilities_current', 'trade_payables',
            'other_financial_liabilities_current', 'other_current_liabilities', 'st_provisions',
            'current_tax_liabilities', 'liabilities_held_for_sale']),
        ('total_tax_expense', ['tax_expense', 'deferred_tax']),
        ('cash', ['cash_and_bank_balances'])
    ) AS t(total, accounts)
);

-- Where a raw value was printed: `sheet!cell` for Excel, `page N` for PDF.
CREATE MACRO source_ref(sheet, cell, page) AS coalesce(sheet || '!' || cell, 'page ' || page);
