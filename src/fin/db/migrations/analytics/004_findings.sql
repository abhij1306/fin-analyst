-- Findings (fin.findings): per company, documents held with open findings (kept out of the metrics), and findings
-- still open or accepted (kept as filed). Rebuilt by every `fin metrics refresh`.
ALTER TABLE companies ADD COLUMN held_documents BIGINT;
ALTER TABLE companies ADD COLUMN open_findings BIGINT;
ALTER TABLE companies ADD COLUMN accepted_findings BIGINT;
