-- Audit log, shared/audit.duckdb (PLAN.md §8). Append-only: fin only ever INSERTs into these tables.
-- Timestamps are UTC. JSON values are stored as text.

-- Each run appends a 'start' row when it begins and an 'end' row when it finishes. The end row repeats the start
-- fields, so it alone describes a finished run. A run with a start row and no end row did not finish.
CREATE TABLE runs (
    run_id VARCHAR NOT NULL,
    record VARCHAR NOT NULL CHECK (record IN ('start', 'end')),
    command VARCHAR NOT NULL,
    args VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    status VARCHAR NOT NULL CHECK (status IN ('started', 'ok', 'failures', 'error', 'blocked')),
    exit_code INTEGER,
    loaded INTEGER,
    skipped INTEGER,
    failed INTEGER,
    pending_review INTEGER,
    git_commit VARCHAR,
    fin_version VARCHAR NOT NULL,
    duckdb_version VARCHAR NOT NULL,
    python_version VARCHAR NOT NULL,
    PRIMARY KEY (run_id, record)
);

-- Structured events. seq orders the events of one run.
CREATE TABLE events (
    run_id VARCHAR NOT NULL,
    seq INTEGER NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    stage VARCHAR NOT NULL,
    file VARCHAR,
    level VARCHAR NOT NULL CHECK (level IN ('info', 'warning', 'error')),
    error_code VARCHAR,
    message VARCHAR NOT NULL,
    context VARCHAR,
    PRIMARY KEY (run_id, seq)
);

-- One row per database file a migration run touched. Paths are relative to the data folder; backup_path is NULL
-- when the file did not exist before (there was nothing to back up).
CREATE TABLE migrations (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    db_file VARCHAR NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    backup_path VARCHAR,
    status VARCHAR NOT NULL CHECK (status IN ('applied', 'failed')),
    error_code VARCHAR,
    message VARCHAR
);
