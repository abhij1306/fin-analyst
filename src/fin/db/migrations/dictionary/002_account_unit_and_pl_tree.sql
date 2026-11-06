-- Account units and a fuller subtotal tree (real annual reports). `fin init` then adds the new accounts and labels
-- from templates/dictionary; this migration changes the rows it never changes.

-- unit: 'rupees' for money; 'rupees_per_share' for earnings per share, stored as printed (not scaled by the
-- statement's unit) and never added into a subtotal.
ALTER TABLE accounts ADD COLUMN unit VARCHAR DEFAULT 'rupees';

-- Balance sheet: non-current and current liabilities add into total liabilities.
UPDATE accounts SET parent = 'total_liabilities', sign = 1
WHERE account IN ('total_non_current_liabilities', 'total_current_liabilities');

-- Profit and loss: income - expenses = profit before exceptional items; +/- exceptional items = profit before
-- extraordinary items; - extraordinary items = PBT; PBT - total tax = profit from continuing operations; + profit
-- from discontinued operations = PAT; PAT + OCI = total comprehensive income.
UPDATE accounts SET parent = 'profit_before_exceptional_items' WHERE account IN ('total_income', 'total_expenses');
UPDATE accounts SET parent = 'profit_before_extraordinary_items' WHERE account = 'exceptional_items';
UPDATE accounts SET parent = 'profit_continuing_operations', sign = 1 WHERE account = 'pbt';
UPDATE accounts SET parent = 'total_tax_expense', sign = 1 WHERE account IN ('tax_expense', 'deferred_tax');
UPDATE accounts SET parent = 'total_comprehensive_income', sign = 1 WHERE account = 'pat';
