-- Claude readings: figures Claude read from a document's pages or cells where fin could not read them
-- (`fin ingest --reading`), and `fin verify`, which compares an independent reading with what is loaded.
--
-- facts.read_by: 'fin' (fin's own extraction) or 'claude' (from a reading file). Either way the fact points at the
-- original document's page or sheet!cell through raw_values.
-- facts.verified: true once `fin verify` found a reading that agrees with the fact in a loaded document (so its
-- validation gates passed). A fact read by Claude is verified only by a reading other than the one it was loaded
-- from. A later verify that differs sets it back to false.
-- documents.reading_sha256 / reading_path: the reading file a document was loaded with (archived under
-- archive/<company>/), NULL when fin read the whole document.

ALTER TABLE facts ADD COLUMN read_by VARCHAR DEFAULT 'fin';
UPDATE facts SET read_by = 'fin';
ALTER TABLE facts ADD COLUMN verified BOOLEAN DEFAULT false;
UPDATE facts SET verified = false;

ALTER TABLE documents ADD COLUMN reading_sha256 VARCHAR;
ALTER TABLE documents ADD COLUMN reading_path VARCHAR;
