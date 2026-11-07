-- Accounts the user accepted in review (`fin review confirm` of a new-account proposal). source is 'seed' for
-- templates/dictionary/schedule_iii.csv and 'review' for an account added in review; run_id is the run that added a
-- review account (NULL for seed accounts).
ALTER TABLE accounts ADD COLUMN source VARCHAR DEFAULT 'seed';
ALTER TABLE accounts ADD COLUMN run_id VARCHAR;
