-- F6 GST and income tax (docs/fdd.md F6): GST return summaries, tax ledger classes and the tax charge.
-- Loaders: fin.fdd.gst (returns), fin.fdd.tax_ledgers (classes); reconciliations: fin.reconcile.gst.

-- GST return summaries as filed (`fin ingest <co> <file> --kind gst --profile gstr1|gstr3b`): one row per return month,
-- with its file + sheet!row. source_files.kind is the return ('gstr1' or 'gstr3b'), so a later file of the same return and
-- period supersedes the earlier. Amounts in rupees as filed. taxable_value, igst, cgst, sgst, cess: outward taxable
-- supplies and the tax on them. GSTR-3B only (NULL for GSTR-1): itc (input tax credit claimed), rcm_liability (tax on
-- inward supplies under reverse charge), paid_cash and paid_credit (tax paid in cash and through the credit ledger).
CREATE TABLE gst_returns (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    return_type VARCHAR NOT NULL CHECK (return_type IN ('gstr1', 'gstr3b')),
    period VARCHAR NOT NULL,
    month DATE NOT NULL,  -- the first day of the return month
    taxable_value DECIMAL(20, 2) NOT NULL,
    igst DECIMAL(20, 2) NOT NULL,
    cgst DECIMAL(20, 2) NOT NULL,
    sgst DECIMAL(20, 2) NOT NULL,
    cess DECIMAL(20, 2) NOT NULL,
    itc DECIMAL(20, 2),
    rcm_liability DECIMAL(20, 2),
    paid_cash DECIMAL(20, 2),
    paid_credit DECIMAL(20, 2),
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row"),
    UNIQUE (file_id, month)
);

-- The tax class of a trial balance ledger (fin.fdd.tax_ledgers): Claude proposes (`fin map tax <co> <file>`), the user
-- confirms or rejects (`fin review confirm|reject <co> <ids> --tax`). Only confirmed classes are read.
--   gst_output  output GST (credits: tax booked on sales);   gst_input   input GST (debits: credit taken);
--   rcm         GST payable under reverse charge (credits);  tds         tax deducted at source;
--   income_tax  income tax provision or advance tax;         mat_credit  MAT credit entitlement.
CREATE SEQUENCE tax_ledgers_seq START 1;
CREATE TABLE tax_ledgers (
    tax_ledger_id INTEGER PRIMARY KEY DEFAULT nextval('tax_ledgers_seq'),
    ledger VARCHAR NOT NULL UNIQUE,
    status VARCHAR NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    tax_class VARCHAR CHECK (tax_class IN ('gst_output', 'gst_input', 'rcm', 'tds', 'income_tax', 'mat_credit')),
    confidence DOUBLE,
    reason VARCHAR,
    run_id VARCHAR NOT NULL,  -- the run of the last change
    changed_at TIMESTAMP NOT NULL
);

-- The closing balance (debit less credit) of each confirmed tax ledger in each loaded trial balance of the latest
-- engagement, with its source row.
CREATE VIEW tax_ledger_balances AS
SELECT
    s.period, s.basis, x.tax_class, t.ledger,
    t.closing_debit - t.closing_credit AS balance,
    s.file_name || ' ' || t.sheet || '!' || t."row" AS source
FROM tax_ledgers x
JOIN trial_balance t ON t.ledger = x.ledger AND t.row_type = 'ledger'
JOIN source_files s ON s.file_id = t.file_id AND s.status = 'loaded' AND s.kind = 'trial_balance'
WHERE x.status = 'confirmed' AND s.engagement_id = (SELECT max(engagement_id) FROM engagement);

-- The tax charge per annual period and scope, from the statements (current_facts): profit before tax, current tax
-- (tax_expense: current tax with earlier years and MAT), deferred tax, the total tax expense (as printed, else current
-- plus deferred, a line not printed counting as nil, as the gross margin does), the effective tax rate (total / PBT, a fraction, NULL when PBT is nil or
-- missing), the net deferred tax asset (assets less liabilities; NULL when neither is printed) and its movement
-- from the year before (NULL without the year before). mat_credit: the confirmed mat_credit ledgers' balance in the
-- period's trial balance (standalone only; NULL when none is confirmed). Nothing is guessed.
CREATE VIEW tax_charge AS
WITH f AS (
    SELECT
        period, scope,
        max(value) FILTER (WHERE account = 'pbt') AS pbt,
        max(value) FILTER (WHERE account = 'tax_expense') AS current_tax,
        max(value) FILTER (WHERE account = 'deferred_tax') AS deferred_tax,
        max(value) FILTER (WHERE account = 'total_tax_expense') AS printed_total,
        max(value) FILTER (WHERE account = 'deferred_tax_assets') AS dta,
        max(value) FILTER (WHERE account = 'deferred_tax_liabilities') AS dtl
    FROM current_facts
    WHERE period_months = 12 AND NOT contains(period, '-')
    GROUP BY period, scope
), t AS (
    SELECT
        *,
        CASE WHEN printed_total IS NOT NULL THEN printed_total
            WHEN current_tax IS NOT NULL OR deferred_tax IS NOT NULL
            THEN coalesce(current_tax, 0) + coalesce(deferred_tax, 0) END AS total_tax,
        CASE WHEN dta IS NOT NULL OR dtl IS NOT NULL THEN coalesce(dta, 0) - coalesce(dtl, 0) END AS net_deferred_tax
    FROM f
), m AS (
    SELECT period, sum(balance) AS mat_credit FROM tax_ledger_balances WHERE tax_class = 'mat_credit' GROUP BY period
)
SELECT
    t.period, t.scope, t.pbt, t.current_tax, t.deferred_tax, t.total_tax,
    div6(CAST(t.total_tax * 100 AS HUGEINT), CAST(t.pbt * 100 AS HUGEINT)) AS effective_tax_rate,
    t.dta AS deferred_tax_assets, t.dtl AS deferred_tax_liabilities, t.net_deferred_tax,
    t.net_deferred_tax - p.net_deferred_tax AS deferred_tax_movement,
    CASE WHEN t.scope = 'standalone' THEN m.mat_credit END AS mat_credit
FROM t
LEFT JOIN t p ON p.scope = t.scope AND p.period = prior_fy(t.period)
LEFT JOIN m ON m.period = t.period
ORDER BY t.scope, t.period;
