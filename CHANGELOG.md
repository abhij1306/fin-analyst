# Changelog

All notable changes to this project. Versions follow semantic versioning; `v1.0.0` is tagged in Phase 12.

## [Unreleased]

### Phase 0: Repo, install, guardrails

Added
- `fin` CLI (Typer) with `fin --version`, `fin init` (creates the data folder tree from PLAN.md §6, idempotent, never
  deletes) and `fin doctor` (checks Python 3.12, uv, node, quarto and the data folder; `--json` output; exit 1 on
  failure).
- Data folder resolution: `FIN_DATA_DIR`, defaulting to `~/FinAnalysis`.
- Error classes for E001, E002, E003 and E701, plus exit-code constants.
- Claude Code hooks in `.claude/settings.json`:
  - `guard.py` (PreToolUse) blocks with E701: `rm`/`mv`/`rmdir`/`unlink`/`find -delete`, redirects and `tee` into
    the data folder; any direct access to a data-folder `.duckdb` file except through `fin`; and the human-only
    `fin delete` and `fin review confirm`. It fails closed on unreadable input and logs every block.
  - `log_action.py` (PostToolUse) appends every tool call to `<data>/logs/claude_actions.jsonl`.
  - A permissions deny list for the human-only commands.
- `install.sh` and `Brewfile` (uv, node, quarto), with the log in `<data>/logs/install.log`.
- CI: ruff and shellcheck (ubuntu), pytest (macOS), and an install job (macOS) that runs `install.sh` twice and checks
  that the second run changes nothing.
- README install section, MIT license.

Dependencies (exact pins in `uv.lock`)
- `typer` 0.28.0: CLI framework (frozen stack).
- Dev: `pytest` 9.2.0, `ruff` 0.17.4 (frozen stack); `pytest-timeout` 2.4.0, so a hung test fails fast in CI.

Notes and deviations
- Homebrew cannot pin exact versions, so `fin doctor` enforces minimums for system tools: uv ≥ 0.11, node ≥ 24,
  quarto ≥ 1.7. Python dependencies are pinned exactly.
- Phase 0 commands do not yet write run records or audit events. The audit logger arrives in Phase 1, which wires
  every command into it.
- `fin init` creates folders only. `config.toml` is created in Phase 1, and databases in Phase 2 (when `fin doctor`
  also gains the schema-version check).
- `install.sh` leaves out `npm ci --prefix dashboards` until the dashboards project exists (Phase 10).

### Guardrails: confirm instead of block

Guardrails now exist only to prevent accidental changes or deletions of data; they never stop Claude doing what the
user asked.

Changed
- `guard.py` (PreToolUse) no longer blocks. For every guarded action (`rm`/`mv`/`rmdir`/`unlink`/`find -delete`,
  redirects and `tee` into the data folder; direct access to a data-folder `.duckdb` file outside `fin`; `fin delete`;
  `fin review confirm`) it exits 0 and prints the Claude Code decision `permissionDecision: "ask"` with an E701
  reason, so the user gets a confirmation prompt. Unreadable input also gets `ask`. Each one is logged to
  `claude_actions.jsonl` with `"decision": "ask"` (replacing `"blocked": true`).
- `.claude/settings.json`: the `permissions.deny` list is removed; the hook is the single mechanism.
- E701 now means "Action needs user confirmation (guard)". `GuardBlocked` keeps its name and exit code 3 for a
  CLI-level refusal.
- PLAN.md: purpose statement (§1); new policy in §4, §8, §9, §11; planned `fin sql --write` (Phase 8),
  non-interactive `fin review` (Phase 5) and `fin delete --yes` (Phase 11); the synthetic documents in
  `sample-financials/` become the Phase 3 fixtures, with the broken-file table in Phase 6.
- CLAUDE.md and README describe confirm-not-block; CLAUDE.md gains a short "Customising" note.
- `.gitignore`: `.claude/worktrees/` and `sample-financials/` (local until Phase 3 moves it into `tests/fixtures/`).

### Phase 1: Foundation: config, audit, errors, DB access

Added
- `config.toml` in the data folder (`fin.config`): display unit, default scope, fiscal-year start month and the four
  validation tolerances (balance sheet, subtotal, cash flow, cross-document; rupees, as exact decimals). `fin init`
  writes the default once and never overwrites it. `load_config` reads it strictly: a missing file is E001, and
  invalid TOML, a missing or unknown key, or a bad value is E002. There are no fallback values.
- Every PLAN.md §9 code as a `FinError` subclass in `fin.audit.errors`. Exit codes: E0xx, E5xx and E601–E603 stop
  the command (2); E1xx–E4xx (per-document failures, via `DocumentFailure`) and E604 complete with failures (1);
  E701 is blocked (3).
- Audit logger (`fin.audit.logger`). Every command runs through `execute(command, args, body)` and gets a `run_id`
  (`<UTC yyyymmddThhmmssZ>-<8 hex>`). It appends a `runs` start row and end row (status, exit code, summary counts,
  git commit, and fin, DuckDB and Python versions), structured `events`, and any other audit rows (`migrations`) to
  `shared/audit.duckdb`, and writes each record to `logs/<date>/<run_id>.jsonl` at once. It ends with
  `summary: loaded N, skipped N, failed N, pending review N, run_id <id>` on stderr. A raised `FinError` is printed,
  logged as an error event with its code, and becomes the exit code. Any other exception is logged and re-raised.
- `fin.db.conn.connect(path, write=False, retry=LockRetry())`: short-lived DuckDB connections, read-only by default.
  A write through a read-only connection raises E502. A locked file is retried 5 times in total with exponential
  backoff (0.2 s, doubling) and then raises E501.
- Migration framework (`fin.db.migrate`, `src/fin/db/migrations/<kind>/NNN_name.sql`), with kinds `audit` (for
  `shared/audit.duckdb`) and `company` (for each `companies/*.duckdb`; no migrations yet). Every file is checked
  first, and a file newer than the code raises E004 before anything changes. Each file with pending migrations is
  copied to `backups/<run_id>/<path>`. Each migration runs in its own transaction and appends a `schema_version`
  row. On failure the file is restored from the backup (or a new file is removed), the failure is recorded, and E503
  is raised. The audit `migrations` table records every file migrated.
- Audit schema 001: `runs`, `events`, `migrations` (append-only; fin only INSERTs).
- `fin migrate` (idempotent). `fin init` now also writes `config.toml` and creates or migrates the databases.
  `fin doctor` now also checks `config.toml` and the audit schema version (E001, E002 or E004 with the fix).
- CI install job: `logs/` is left out of the "second install changes nothing" comparison, because each run appends
  its log. The job also checks that `config.toml` is byte-identical and the audit database is up to date.

Dependencies (exact pins in `uv.lock`)
- `duckdb` 2.0.0: the database (frozen stack); used for the audit database and later for all company and shared
  databases.

Notes and deviations
- §9 has no code for config or local-database problems, so they reuse E001 (missing: `config.toml`, audit database,
  a database opened read-only that does not exist) and E002 (wrong shape: invalid `config.toml`, audit schema older
  than the code). Failing to open a database for any reason other than a lock raises E003.
- Append-only `runs`: one table with a `record` column (`start` or `end`). The end row repeats the start fields, so
  a finished run is `WHERE record = 'end'`, and a run with no end row did not finish.
- Audit rows are written to DuckDB when the run starts and ends (events are buffered between). The JSONL log is
  written per record. This keeps lock time on the shared audit file short. Before `fin init` has created the audit
  database, records are held in memory and written at the end of the run. A run that still cannot be recorded (for
  example `fin doctor` before `fin init`) says so on stderr.
- Backups go to `backups/<run_id>/` (the run_id starts with the UTC timestamp), which links each backup to its run.
- Only `runs`, `events` and `migrations` exist so far. The other §8 audit tables (`mapping_decisions`,
  `validation_results`, `queries`, `reports`, `deletions`) arrive as audit migrations in the phases that write them.
- `git_commit` is NULL when fin is not run from a git checkout.
- The summary line goes to stderr so that `--json` output on stdout stays parseable.

Human follow-ups
- Confirm the default tolerances (100000.00 rupees, i.e. one lakh, for each check) and the default display unit
  (crores) before Phase 6 uses them.
- Consider adding dedicated §9 codes for config and local-database problems instead of reusing E001 and E002.

### Phase 4: PDF bake-off and extraction

Added
- `fin.extract.pdf.extract_pdf(path)`: reads every value from the balance sheet, profit and loss, cash flow and note
  tables of a text PDF annual report. Each `PdfValue` has the label, fiscal year, raw text, `Decimal` value in
  document units and in rupees, unit, 1-based page, bbox (points from the page's top-left, pdfplumber's
  convention), statement, section heading and note reference. `PdfExtraction` adds scope (standalone or
  consolidated), the fiscal years found and the unit. Columns come from word positions, so tables with and without
  ruled lines work the same way. Units: rupees, thousands, lakhs (lacs), millions, crores.
- Errors: E101 (not a readable PDF), E102 (encrypted), E104 (no text layer), E201 (no statement tables), E202 (a
  statement page with no period headers), E203 (a statement page with no unit).
- `fin.extract.text.write_notes_markdown(path, out_dir)`: writes `<pdf stem>.auditor_report.md` and
  `<pdf stem>.notes.md` (bold lines become headings, note tables become Markdown tables), each starting with its
  source file and pages. Later wiring writes these into `notes/<company>/`.
- `fin.extract.pdf.render_crop(path, page, bbox, out)`: PNG of the full-width band around a value with the value
  highlighted, for evidence pages (Phase 8).
- `scripts/pdf_bakeoff.py`: scores pdfplumber against pdf-inspector on exact-value match with an answer key.
  pdfplumber 236/236 (100%), pdf-inspector 220/236 (93.2%); decision and licenses in PLAN.md "Phase 4 decision".
- Synthetic Tarang PDFs, `answer_key.json` and the fixture README in `tests/fixtures/sample-financials/`.

Dependencies (exact pins in `uv.lock`)
- `pdfplumber` 0.11.10 (MIT): PDF tables, text and page rendering; the bake-off winner. Brings in pdfminer.six (MIT),
  pypdfium2 (Apache-2.0/BSD-3-Clause) and Pillow.
- Dev: `pdf-inspector` 1.25.2 (MIT): the bake-off's other contender, used only by `scripts/pdf_bakeoff.py`.

Notes and deviations
- PDFs are not wired into `fin ingest` yet: Phases 2-3 (schema, ingest) are built in parallel, so the extractor is a
  standalone module that returns plain data. Wiring it in, with run records and audit events, is the next step.
- Unit parsing is private to `fin.extract.pdf`; merge it with the Excel extractor's once both are on main.
- Notes go to Markdown with pdfplumber rather than AnyDoc or pdf-inspector, to keep one PDF library.
- Wrapped (multi-line) row labels are not joined yet; the synthetic reports have none.

Human follow-ups
- Run the bake-off on 3-5 real annual reports (small listed Indian manufacturers, BSE/NSE filings) kept in the
  private test set, with a key in the `answer_key.json` shape, and record the scores in PLAN.md.
