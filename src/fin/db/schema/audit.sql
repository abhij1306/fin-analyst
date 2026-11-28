-- Audit log, shared/audit.duckdb (docs/product.md §8). Append-only: fin only ever INSERTs into these tables.
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

-- Every mapping decision. actor is 'user' or 'ai:<provider>' (fin.actor; proposals are 'ai:claude'); on_request is
-- true when an AI decided because the user asked (for example `fin review confirm` run by Claude).
CREATE TABLE mapping_decisions (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR,
    statement VARCHAR,
    section VARCHAR,
    raw_label VARCHAR NOT NULL,
    account VARCHAR,
    scope VARCHAR,
    decision VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    on_request BOOLEAN NOT NULL DEFAULT false,
    confidence DOUBLE,
    reason VARCHAR
);

-- One row per validation check run on a document.
CREATE TABLE validation_results (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL,
    file_name VARCHAR,
    check_name VARCHAR NOT NULL,
    fiscal_year VARCHAR,
    scope VARCHAR,
    expected DECIMAL(20, 2),
    actual DECIMAL(20, 2),
    tolerance DECIMAL(20, 2),
    passed BOOLEAN NOT NULL,
    error_code VARCHAR,
    message VARCHAR
);

-- Questions answered with `fin sql`, and data changes made with `fin sql --write`.
CREATE TABLE queries (
    query_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR,
    question VARCHAR,
    sql VARCHAR NOT NULL,
    mode VARCHAR NOT NULL CHECK (mode IN ('read', 'write')),
    row_count BIGINT,
    result_hash VARCHAR,
    evidence_path VARCHAR,
    backup_path VARCHAR
);

-- Rendered reports.
CREATE TABLE reports (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    output_paths VARCHAR NOT NULL,
    template_commit VARCHAR,
    data_hash VARCHAR NOT NULL,
    narrative_path VARCHAR
);

-- Company deletions.
CREATE TABLE deletions (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    files_removed VARCHAR NOT NULL,
    row_counts VARCHAR,
    backup_path VARCHAR NOT NULL
);

-- Findings: a difference worth showing the user, not a failure (fin.findings; `fin verify` and the validation
-- gates E401-E405 record them). Append-only like the other audit tables.
--
-- check_name: the check ('verify_reading' for `fin verify`). code: the check's §9 code (E401-E405), NULL for
-- verify_reading. finding_id: the finding's id in the company database's findings table (with company, the key).
-- finding: for verify_reading 'differ' (both values), 'missing_in_fin' (a line, or a whole statement, the document
-- did not load) or 'missing_in_reading' (a loaded line of a statement, scope and period the reading covers that the
-- reading does not have).
-- fin_* is the loaded figure, other_* what it was compared with (a reading, another document or a computed total);
-- difference is other_value - fin_value when both exist.
-- status: open when found; a status change (accepted, resolved, superseded) appends another row with the same
-- finding_id. NULL for verify_reading findings, which are not tracked.
CREATE TABLE findings (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    check_name VARCHAR NOT NULL,
    finding VARCHAR NOT NULL,
    doc_id INTEGER,
    file_name VARCHAR,
    statement VARCHAR,
    scope VARCHAR,
    period VARCHAR,
    label VARCHAR,
    fin_value DECIMAL(20, 2),
    other_value DECIMAL(20, 2),
    difference DECIMAL(20, 2),
    fin_printed VARCHAR,
    other_printed VARCHAR,
    fin_source VARCHAR,
    other_source VARCHAR,
    other_file VARCHAR,
    other_sha256 VARCHAR,
    fact_id BIGINT,
    read_by VARCHAR,
    message VARCHAR,
    mappings VARCHAR,
    finding_id BIGINT,
    code VARCHAR,
    status VARCHAR
);

-- Figures the user states (fin.facts, `fin fact state|unstate`). action: 'stated' (stored), 'finding' (a loaded
-- document gives the figure: nothing stored, finding_id is the finding recorded) or 'withdrawn'. stated_id is the
-- company database's stated_figures.stated_id (NULL for 'finding'); backup_path the company database copied first.
CREATE TABLE stated_figures (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    action VARCHAR NOT NULL CHECK (action IN ('stated', 'finding', 'withdrawn')),
    stated_id BIGINT,
    finding_id BIGINT,
    account VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    value DECIMAL(20, 2) NOT NULL,
    reason VARCHAR,
    actor VARCHAR NOT NULL,
    backup_path VARCHAR NOT NULL
);

-- Corrections of a figure fin read wrongly (fin.facts, `fin fact correct`): correction_id and fact_id are the company
-- database's; actor ('user' or 'ai:<provider>', fin.actor) and on_request as in mapping_decisions; backup_path the
-- company database copied first.
CREATE TABLE fact_corrections (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    correction_id BIGINT NOT NULL,
    fact_id BIGINT NOT NULL,
    account VARCHAR,
    period VARCHAR,
    scope VARCHAR NOT NULL,
    original_value DECIMAL(20, 2) NOT NULL,
    value DECIMAL(20, 2) NOT NULL,
    reason VARCHAR NOT NULL,
    actor VARCHAR NOT NULL,
    on_request BOOLEAN NOT NULL,
    backup_path VARCHAR NOT NULL
);

-- Diligence (fin.diligence): every engagement, accountant query and adjustment added, and every status change, and
-- every exchange rate the user states. kind: 'engagement', 'query', 'adjustment', 'gate' or 'rate'; item_id: its id in
-- the company database (engagement_id, query_id, adj_id or rate_id; status 'stated' for a rate), or for a gate its
-- name ('G1'); status: its status after the change ('approved' for a gate approval);
-- detail: the whole row as it is now (JSON; for a gate, its gate_approvals row).
CREATE TABLE diligence_events (
    run_id VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    company VARCHAR NOT NULL,
    engagement_id INTEGER NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('engagement', 'query', 'adjustment', 'gate', 'rate')),
    item_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    detail VARCHAR NOT NULL
);

-- Background jobs of the web UI (fin.services.jobs): a long action (an export, later ingest and reports) runs in one
-- worker thread while the page shows its progress line. Jobs run one at a time; a later one waits as 'queued'. A job
-- still queued or running when the server stopped is marked 'interrupted' when it starts again. lines is the result's
-- lines as JSON; code its error code when refused (or E012 when it failed); run_id the run the action recorded.
CREATE SEQUENCE jobs_seq START 1;
CREATE TABLE jobs (
    job_id BIGINT PRIMARY KEY DEFAULT nextval('jobs_seq'),
    company VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    arguments VARCHAR NOT NULL,
    state VARCHAR NOT NULL CHECK (state IN ('queued', 'running', 'done', 'refused', 'failed', 'interrupted')),
    progress VARCHAR,
    lines VARCHAR,
    code VARCHAR,
    run_id VARCHAR,
    queued_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    ended_at TIMESTAMP
);
