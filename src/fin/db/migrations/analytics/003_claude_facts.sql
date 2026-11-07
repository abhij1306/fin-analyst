-- Claude readings: per company, how many current facts were read by Claude (`fin ingest --reading`) and are not yet
-- verified by an independent reading (`fin verify`). Rebuilt by every `fin metrics refresh`.
ALTER TABLE companies ADD COLUMN unverified_claude_facts BIGINT;
