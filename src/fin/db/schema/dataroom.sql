-- Data-room intake (SPEC-13): the plan `fin dataroom scan` writes. Nothing here is loaded data.
-- One row per distinct file content (sha256): a file with the same content at another path is listed in duplicates.
-- A link has no content: sha256 NULL. Paths and file names are stored as filed, like source_files.file_name.
-- path: shown path, relative to the folder scanned ("<archive path>/<entry path>" inside an unpacked archive), "/"
-- separators. location: the file on disk. kind: a column profile's kind, statements, archive or unknown.
-- reason, next_step: why fin cannot use the file and what to do (fin.dataroom.kinds.REASONS); NULL when it can.
CREATE SEQUENCE dataroom_files_seq START 1;
CREATE TABLE dataroom_files (
    file_id INTEGER PRIMARY KEY DEFAULT nextval('dataroom_files_seq'),
    sha256 VARCHAR,
    folder VARCHAR NOT NULL,
    path VARCHAR NOT NULL,
    location VARCHAR NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('trial_balance', 'ledger_vouchers', 'registers', 'payroll', 'bank',
        'orderbook', 'gst', 'statements', 'archive', 'unknown')),
    profile VARCHAR,
    period VARCHAR,
    reason VARCHAR,
    next_step VARCHAR,
    duplicates VARCHAR[] NOT NULL,
    run_id VARCHAR NOT NULL,
    scanned_at TIMESTAMP NOT NULL,
    CHECK ((reason IS NULL) = (next_step IS NULL))
);
