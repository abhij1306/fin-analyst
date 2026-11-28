-- Company database: FDD F3, provisions, payroll liabilities and cut-off (docs/fdd.md F3, docs/decisions/fdd-f3.md).
-- Run after the diligence and source files (it reads engagement, source_files, trial_balance, register_lines,
-- payroll_lines, payroll_units, recon_runs, recon_items and recon_match_lines). Money is rupees. Nothing here
-- changes company data: the views compare what is loaded, and differences are queried, never fixed.

-- Trial balance ledgers that hold a provision F3 compares with: 'doubtful_debts' (provision for doubtful debts,
-- expected credit loss allowance) or 'gratuity'. The dictionary has no account for these (they sit inside
-- provisions or trade receivables), so Claude proposes the ledgers (`fin provisions propose`) and the user confirms
-- or rejects them (`fin provisions confirm|reject`); only confirmed ledgers count. One row per ledger; a new proposal on a rejected ledger replaces it.
CREATE SEQUENCE provision_ledgers_seq START 1;
CREATE TABLE provision_ledgers (
    provision_id INTEGER PRIMARY KEY DEFAULT nextval('provision_ledgers_seq'),
    ledger VARCHAR NOT NULL UNIQUE,
    kind VARCHAR NOT NULL CHECK (kind IN ('doubtful_debts', 'gratuity')),
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    reason VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,  -- the run of the last change
    changed_at TIMESTAMP NOT NULL
);

-- The year ends of the latest engagement's annual periods (FY25 with an April start: 2025-03-31).
CREATE VIEW fdd_year_ends AS
WITH p AS (
    SELECT engagement_id, fiscal_start_month, unnest(string_split(periods, ',')) AS period
    FROM engagement WHERE engagement_id = (SELECT max(engagement_id) FROM engagement)
)
SELECT engagement_id, period,
    make_date(2000 + CAST(substr(period, 3) AS INTEGER) + CASE WHEN fiscal_start_month = 1 THEN 1 ELSE 0 END,
        fiscal_start_month, 1) - 1 AS year_end
FROM p;

-- The confirmed provision ledgers' balances in each loaded trial balance of the latest engagement (an annual period
-- or the stub): credit less debit, so a provision reads positive. One row per period and kind; provision_inr is NULL
-- when no ledger of that kind is confirmed, and also when the confirmed ones are not in that trial balance (missing,
-- not zero). `ledgers` of `confirmed_ledgers` are in it: `fin provisions show` warns when fewer are.
CREATE VIEW provision_balances AS
WITH tb AS (
    SELECT file_id, file_name, period FROM source_files
    WHERE kind = 'trial_balance' AND status = 'loaded' AND engagement_id = (SELECT max(engagement_id) FROM engagement)
),
kinds AS (SELECT * FROM (VALUES ('doubtful_debts'), ('gratuity')) k(kind))
SELECT tb.period, k.kind,
    (SELECT sum(t.closing_credit - t.closing_debit) FROM trial_balance t
        JOIN provision_ledgers c ON c.ledger = t.ledger AND c.kind = k.kind AND c.status = 'confirmed'
        WHERE t.file_id = tb.file_id AND t.row_type = 'ledger') AS provision_inr,
    (SELECT count(*) FROM trial_balance t
        JOIN provision_ledgers c ON c.ledger = t.ledger AND c.kind = k.kind AND c.status = 'confirmed'
        WHERE t.file_id = tb.file_id AND t.row_type = 'ledger') AS ledgers,
    (SELECT count(*) FROM provision_ledgers c WHERE c.kind = k.kind AND c.status = 'confirmed') AS confirmed_ledgers,
    tb.file_name AS trial_balance_file
FROM tb CROSS JOIN kinds k;

-- Sales invoices open at each year end, from the latest bank_vs_invoices run of each period (D5). Per match group,
-- the receipts dated by the year end are set against its invoices dated by then, in the group's currency; what is left (none when
-- they cover it) is open, shared over the group's invoices by their amount and shown in their Amount in Rs. So an
-- unmatched invoice is open in full, one paid after the year end too, and a partly paid or instalment-paid one for
-- the part not received by then. An invoice in several runs counts from the latest. Only year ends whose period has
-- a run are aged (a period with no bank statement cannot tell what was paid). age_days: year end less invoice date.
CREATE VIEW receivable_open_lines AS
WITH runs AS (
    SELECT recon_id, period FROM recon_runs
    WHERE name = 'bank_vs_invoices' AND engagement_id = (SELECT max(engagement_id) FROM engagement)
    QUALIFY row_number() OVER (PARTITION BY period ORDER BY recon_id DESC) = 1
),
lines AS (
    SELECT m.* FROM recon_match_lines m JOIN runs USING (recon_id)
    JOIN recon_items i ON i.recon_id = m.recon_id AND i.item_no = m.item_no
    WHERE i.kind <> 'fx'
),
inv AS (
    SELECT * FROM lines WHERE side = 'invoice'
    QUALIFY row_number() OVER (PARTITION BY file_id, sheet, "row" ORDER BY recon_id DESC) = 1
),
aged AS (
    SELECT y.period, y.year_end, inv.party AS customer, inv.reference AS invoice_no, inv.line_date AS invoice_date,
        inv.file_id, inv.sheet, inv."row", date_diff('day', inv.line_date, y.year_end) AS age_days,
        inv.amount_inr,
        (SELECT sum(i.amount) FROM lines i WHERE i.side = 'invoice' AND i.recon_id = inv.recon_id
            AND i.item_no = inv.item_no AND i.line_date <= y.year_end) AS invoiced,
        coalesce((SELECT sum(b.amount) FROM lines b WHERE b.side = 'bank' AND b.recon_id = inv.recon_id
            AND b.item_no = inv.item_no AND b.line_date <= y.year_end), 0) AS received
    FROM inv JOIN fdd_year_ends y ON inv.line_date <= y.year_end
    WHERE y.period IN (SELECT period FROM runs)
),
open AS (
    SELECT *,
        CASE
            WHEN received = 0 OR invoiced <= 0 THEN amount_inr
            WHEN received >= invoiced THEN 0
            ELSE CAST(round(amount_inr * (invoiced - received) / invoiced, 2) AS DECIMAL(20, 2))
        END AS open_inr
    FROM aged
)
SELECT period, year_end, customer, invoice_no, invoice_date, file_id, sheet, "row", age_days, open_inr,
    CASE WHEN age_days <= 90 THEN '0-90' WHEN age_days <= 180 THEN '91-180'
        WHEN age_days <= 365 THEN '181-365' ELSE 'over 365' END AS bucket
FROM open WHERE open_inr > 0;

-- Receivables ageing at each year end against the provision for doubtful debts in that year's trial balance. One
-- row per period and bucket ('0-90', '91-180', '181-365', 'over 365', 'total'). share: the bucket's part of the open
-- total. coverage: the provision over the open amount of this bucket and every older one (for 'total', over all of
-- it); NULL with no provision figure or nothing open. Ratios are div6 on integer paise (six decimals). not_in_bank_run_inr: sales invoices dated by the year end
-- that are in no bank_vs_invoices run (their period has no bank statement): not aged, so the ageing is incomplete.
CREATE VIEW receivable_provision AS
WITH buckets AS (
    SELECT * FROM (VALUES (1, '0-90'), (2, '91-180'), (3, '181-365'), (4, 'over 365'), (5, 'total')) b(n, bucket)
),
years AS (SELECT DISTINCT period, year_end FROM receivable_open_lines
    UNION SELECT y.period, y.year_end FROM fdd_year_ends y
    WHERE y.period IN (SELECT period FROM recon_runs WHERE name = 'bank_vs_invoices'
        AND engagement_id = (SELECT max(engagement_id) FROM engagement))),
sums AS (
    SELECT y.period, y.year_end, b.n, b.bucket,
        coalesce(sum(o.open_inr) FILTER (WHERE b.bucket = 'total' OR o.bucket = b.bucket), 0) AS open_inr,
        count(o.open_inr) FILTER (WHERE b.bucket = 'total' OR o.bucket = b.bucket) AS invoices,
        coalesce(sum(o.open_inr) FILTER (WHERE b.bucket = 'total' OR o.bucket IN (
            SELECT x.bucket FROM buckets x WHERE x.n >= b.n AND x.n < 5)), 0) AS open_and_older_inr
    FROM years y CROSS JOIN buckets b
    LEFT JOIN receivable_open_lines o ON o.period = y.period
    GROUP BY y.period, y.year_end, b.n, b.bucket
),
registered AS (
    SELECT r.file_id, r.sheet, r."row", r.invoice_date, r.amount_inr FROM register_lines r
    JOIN source_files s USING (file_id)
    WHERE r.kind = 'sales' AND s.status = 'loaded' AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
),
in_runs AS (
    SELECT DISTINCT m.file_id, m.sheet, m."row" FROM recon_match_lines m JOIN recon_runs r USING (recon_id)
    WHERE r.name = 'bank_vs_invoices' AND m.side = 'invoice'
        AND r.engagement_id = (SELECT max(engagement_id) FROM engagement)
)
SELECT s.period, s.year_end, s.bucket, s.open_inr, s.invoices,
    div6(CAST(s.open_inr * 100 AS BIGINT), CAST(t.open_inr * 100 AS BIGINT)) AS share,
    p.provision_inr,
    CASE WHEN p.provision_inr IS NOT NULL AND s.open_and_older_inr <> 0
        THEN div6(CAST(p.provision_inr * 100 AS BIGINT), CAST(s.open_and_older_inr * 100 AS BIGINT)) END AS coverage,
    (SELECT coalesce(sum(g.amount_inr), 0) FROM registered g
        WHERE g.invoice_date <= s.year_end AND NOT EXISTS (SELECT 1 FROM in_runs i
            WHERE i.file_id = g.file_id AND i.sheet = g.sheet AND i."row" = g."row")) AS not_in_bank_run_inr,
    p.trial_balance_file
FROM sums s
JOIN sums t ON t.period = s.period AND t.bucket = 'total'
LEFT JOIN provision_balances p ON p.period = s.period AND p.kind = 'doubtful_debts'
ORDER BY s.period, s.n;

-- Indicative gratuity per employee, from each loaded payroll snapshot of the latest engagement whose unit the user
-- confirmed annual, at its snapshot date. Not an actuarial valuation: undiscounted, no attrition or salary growth.
-- An employee who resigned on or before the snapshot date is left out. years: completed years from joining to the
-- snapshot date. status: 'eligible' (years >= min_years), 'under_min_years', or 'incomplete' (no joining date or
-- gross salary: not computed, counted). The parameters are config.toml [fdd] (fin.diligence.provisions passes them):
--   gratuity_basic_share: 15/26 x monthly wages x years, capped at cap; monthly wages = gross salary / 12 x
--                         basic_share (the assumed basic pay share);
--   gratuity_wage_code:   the same with wages of at least half the CTC (the wage-code definition: wages are at least
--                         50% of total remuneration, CTC standing for it; gross salary when CTC is blank).
CREATE MACRO gratuity_lines(basic_share, min_years, cap) AS TABLE
WITH files AS (
    SELECT s.file_id, s.file_name, s.period FROM source_files s JOIN payroll_units u USING (file_id)
    WHERE s.kind = 'payroll' AND s.status = 'loaded' AND u.unit = 'annual'
        AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
),
base AS (
    SELECT f.period, f.file_name, p.sheet, p."row", p.employee_code, p.snapshot_date, p.joining_date,
        p.gross_salary, p.ctc,
        CASE WHEN p.joining_date <= p.snapshot_date THEN date_sub('year', p.joining_date, p.snapshot_date) END AS years
    FROM payroll_lines p JOIN files f USING (file_id)
    WHERE p.resign_date IS NULL OR p.resign_date > p.snapshot_date
),
graded AS (
    SELECT *, CASE WHEN years IS NULL OR gross_salary IS NULL THEN 'incomplete'
        WHEN years >= min_years THEN 'eligible' ELSE 'under_min_years' END AS status
    FROM base
)
SELECT *,
    CASE WHEN status = 'eligible' THEN CAST(round(least(
        gross_salary * basic_share * 15 * years / 312, cap), 2) AS DECIMAL(20, 2)) END AS gratuity_basic_share,
    CASE WHEN status = 'eligible' THEN CAST(round(least(
        greatest(gross_salary * basic_share, 0.5 * coalesce(ctc, gross_salary)) * 15 * years / 312, cap), 2)
        AS DECIMAL(20, 2)) END AS gratuity_wage_code
FROM graded;

-- Per payroll snapshot: the indicative gratuity totals (gratuity_lines) against the confirmed gratuity provision
-- ledgers in the same period's trial balance (provision_balances), and the shortfall (indicative less provision;
-- NULL without a provision figure).
CREATE MACRO gratuity_indicative(basic_share, min_years, cap) AS TABLE
SELECT g.period, any_value(g.file_name) AS payroll_file, any_value(g.snapshot_date) AS snapshot_date,
    count(*) AS employees,
    count(*) FILTER (WHERE g.status = 'eligible') AS eligible,
    count(*) FILTER (WHERE g.status = 'incomplete') AS incomplete,
    coalesce(sum(g.gratuity_basic_share), 0) AS gratuity_basic_share,
    coalesce(sum(g.gratuity_wage_code), 0) AS gratuity_wage_code,
    any_value(p.provision_inr) AS provision_inr,
    coalesce(sum(g.gratuity_basic_share), 0) - any_value(p.provision_inr) AS shortfall_basic_share,
    coalesce(sum(g.gratuity_wage_code), 0) - any_value(p.provision_inr) AS shortfall_wage_code,
    any_value(p.trial_balance_file) AS trial_balance_file
FROM gratuity_lines(basic_share, min_years, cap) g
LEFT JOIN provision_balances p ON p.period = g.period AND p.kind = 'gratuity'
GROUP BY g.period ORDER BY g.period;
