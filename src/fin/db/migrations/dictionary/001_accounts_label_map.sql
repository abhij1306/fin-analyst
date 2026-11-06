-- Account dictionary, shared/dictionary.duckdb (PLAN.md §7). `fin init` seeds it from
-- templates/dictionary/schedule_iii.csv (accounts) and templates/dictionary/labels.csv (label_map).

-- statement: balance_sheet, profit_and_loss or cash_flow.
-- parent is the subtotal the account adds into and sign is +1 (adds) or -1 (subtracts); both NULL for a top line.
CREATE TABLE accounts (
    account VARCHAR PRIMARY KEY,
    statement VARCHAR NOT NULL CHECK (statement IN ('balance_sheet', 'profit_and_loss', 'cash_flow')),
    parent VARCHAR,
    sign INTEGER CHECK (sign IN (1, -1)),
    description VARCHAR NOT NULL
);

CREATE SEQUENCE label_map_seq START 1;

-- Normalized label (fin.map.normalize) -> account, within one statement. section is the normalized heading the
-- label must sit under, or '' for any heading: it tells apart labels such as "Borrowings" that appear under both
-- non-current and current liabilities. For a company, a company-scope row wins over a global one.
CREATE TABLE label_map (
    mapping_id INTEGER PRIMARY KEY DEFAULT nextval('label_map_seq'),
    label VARCHAR NOT NULL,
    statement VARCHAR NOT NULL CHECK (statement IN ('balance_sheet', 'profit_and_loss', 'cash_flow')),
    section VARCHAR NOT NULL,
    account VARCHAR NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('global', 'company')),
    company VARCHAR,
    source VARCHAR NOT NULL,        -- 'seed' for templates/dictionary/labels.csv
    created_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL,
    CHECK ((scope = 'global') = (company IS NULL))
);
