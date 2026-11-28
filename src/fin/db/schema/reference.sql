-- Reference documents (SPEC-10): a third party's figures (a banker's or an adviser's databook) kept apart from the
-- company's. Nothing here is a fact: current_facts, metrics, statements and reports never read these tables.
--
-- reference_files: one row per file `fin reference add` loaded. source: the third party as the user names it (as typed;
-- a data-room load names it by the file's path in the room). file_name as filed. archive_path:
-- archive/<company>/reference/<sha256>.<ext>; reading_path: the archived reading file when the assistant read it
-- (read_by 'ai:claude' on its values). unit_given: the unit the user gave for sheets that name none (fin.ingest_read).
CREATE SEQUENCE reference_files_seq START 1;
CREATE TABLE reference_files (
    ref_file_id INTEGER PRIMARY KEY DEFAULT nextval('reference_files_seq'),
    source VARCHAR NOT NULL,
    file_name VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL UNIQUE,
    archive_path VARCHAR NOT NULL,
    reading_path VARCHAR,
    unit_given VARCHAR,
    run_id VARCHAR NOT NULL,
    added_at TIMESTAMP NOT NULL
);

-- reference_lines: one row per distinct line (statement, heading and label, normalized as the dictionary does: section_key,
-- label_key) with its mapping. target_kind 'account' (a dictionary account of the same statement, not per share) or
-- 'metric' (a metric in rupees). status: 'dictionary' (the dictionary maps the label, as it maps a company's fact),
-- 'pending' (nothing yet), 'proposed' (the assistant's, with confidence and reason), 'confirmed' (the user's) or
-- 'rejected'. Only dictionary and confirmed lines are compared. actor: who took the last step (fin.actor).
CREATE SEQUENCE reference_lines_seq START 1;
CREATE TABLE reference_lines (
    line_id INTEGER PRIMARY KEY DEFAULT nextval('reference_lines_seq'),
    statement VARCHAR NOT NULL,
    section_key VARCHAR NOT NULL,
    label_key VARCHAR NOT NULL,
    section VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('dictionary', 'pending', 'proposed', 'confirmed', 'rejected')),
    target_kind VARCHAR CHECK (target_kind IN ('account', 'metric')),
    target VARCHAR,
    confidence DECIMAL(4, 3),
    reason VARCHAR,
    actor VARCHAR,
    run_id VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL,
    UNIQUE (statement, section_key, label_key),
    CHECK ((target IS NULL) = (target_kind IS NULL)),
    CHECK (status IN ('pending', 'rejected') OR target IS NOT NULL)
);

-- reference_values: one row per figure, as fin's reader (or the reading) found it. value in rupees; raw_unit and
-- rupees_per_unit as printed, so the comparison shows the databook's own unit.
CREATE SEQUENCE reference_values_seq START 1;
CREATE TABLE reference_values (
    value_id BIGINT PRIMARY KEY DEFAULT nextval('reference_values_seq'),
    ref_file_id INTEGER NOT NULL,
    line_id INTEGER NOT NULL,
    sheet VARCHAR,
    cell VARCHAR,
    page INTEGER,
    raw_value_text VARCHAR NOT NULL,
    raw_unit VARCHAR NOT NULL,
    rupees_per_unit BIGINT NOT NULL,
    fiscal_year VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    period_months INTEGER NOT NULL,
    period_end DATE NOT NULL,
    scope VARCHAR NOT NULL CHECK (scope IN ('standalone', 'consolidated')),
    value DECIMAL(20, 2) NOT NULL,
    read_by VARCHAR NOT NULL
);

-- Each reference figure beside fin's (`fin reference compare`, the References screen). fin_value: current_facts for an
-- account, metrics for a metric, same period and scope; NULL when fin has none (never 0). difference = fin_value -
-- reference_value, in rupees; the *_shown columns are in the reference's own unit (raw_unit). fin_source: file and
-- sheet!cell or page of the fact, "stated #id" for a figure the user stated, or "metric <name>". state: unmapped (the
-- line is not dictionary or confirmed), no_fin_figure, agrees or differs.
CREATE VIEW reference_comparison AS
WITH ref AS (
    SELECT
        v.value_id, f.ref_file_id, f.source, f.file_name, source_ref(v.sheet, v.cell, v.page) AS reference_ref,
        l.line_id, l.statement, l.label, l.status AS line_status, l.target_kind, l.target, v.period, v.scope,
        v.value AS reference_value, v.raw_unit, v.rupees_per_unit, v.read_by,
        l.status IN ('dictionary', 'confirmed') AS mapped
    FROM reference_values v
    JOIN reference_files f ON f.ref_file_id = v.ref_file_id
    JOIN reference_lines l ON l.line_id = v.line_id
), fin AS (
    SELECT
        r.value_id,
        CASE r.target_kind WHEN 'account' THEN c.value ELSE m.value END AS fin_value,
        CASE
            WHEN m.value IS NOT NULL THEN 'metric ' || r.target
            WHEN c.stated_id IS NOT NULL THEN 'stated #' || c.stated_id
            WHEN s.fact_id IS NOT NULL THEN s.source_file || ' ' || s.source_ref
        END AS fin_source
    FROM ref r
    LEFT JOIN current_facts c
        ON r.mapped AND r.target_kind = 'account' AND c.account = r.target AND c.period = r.period
        AND c.scope = r.scope
    LEFT JOIN fact_sources s ON s.fact_id = c.fact_id
    LEFT JOIN metrics m
        ON r.mapped AND r.target_kind = 'metric' AND m.metric = r.target AND m.period = r.period
        AND m.scope = r.scope
)
SELECT
    r.* EXCLUDE (mapped),
    x.fin_value,
    x.fin_source,
    x.fin_value - r.reference_value AS difference,
    CAST(r.reference_value / r.rupees_per_unit AS DECIMAL(20, 2)) AS reference_shown,
    CAST(x.fin_value / r.rupees_per_unit AS DECIMAL(20, 2)) AS fin_shown,
    CAST((x.fin_value - r.reference_value) / r.rupees_per_unit AS DECIMAL(20, 2)) AS difference_shown,
    CASE
        WHEN NOT r.mapped THEN 'unmapped'
        WHEN x.fin_value IS NULL THEN 'no_fin_figure'
        WHEN x.fin_value = r.reference_value THEN 'agrees'
        ELSE 'differs'
    END AS state
FROM ref r
JOIN fin x ON x.value_id = r.value_id;
