-- Findings from every check (fin.findings), not only `fin verify`: validation gates (E401-E405) record their failed
-- checks here as findings instead of rejecting the document.
-- finding_id: the finding's id in the company database's findings table (with company, the key).
-- code: the check's §9 code (E401-E405), NULL for verify_reading.
-- status: open when found; a status change (accepted, resolved, superseded) appends another row with the same
-- finding_id. NULL on rows recorded before this migration (verify findings, not tracked then).
ALTER TABLE findings ADD COLUMN finding_id BIGINT;
ALTER TABLE findings ADD COLUMN code VARCHAR;
ALTER TABLE findings ADD COLUMN status VARCHAR;
