# Changelog

All notable changes to this project. Versions follow semantic versioning; `v1.0.0` is tagged in Phase 12.

## [1.0.0] - planned, not yet tagged

The first release is the state of `main` after Phase 12's clean-machine test passes. The `v1.0.0` tag is created then, and
the sections below move under this heading.

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

### Phase 2: Schema and dictionary

Added
- Company schema (migration `company/001_documents_facts.sql`): `documents`, `raw_values`, `facts` (value
  `DECIMAL(20,2)` rupees), `notes`, plus `schema_version` from the migration framework. Created when a company is
  first ingested; `fin migrate` keeps existing company files current.
- Shared dictionary database `shared/dictionary.duckdb` (new migration kind `dictionary`): `accounts` (account,
  statement, parent, sign, description) and `label_map`. `fin init` creates it with the audit database, and
  `fin doctor` checks its schema version.
- Seed files: `templates/dictionary/schedule_iii.csv` (75 Schedule III accounts with parent subtotal and sign, covering
  every account in the answer key) and `templates/dictionary/labels.csv` (118 label mappings: the Kestrel/Tarang
  Schedule III wording, Bluepeak's older wording such as "Sundry debtors" and "Reserves and surplus", and common
  synonyms). `fin init` adds rows that are missing (`fin.map.dictionary.load_seed`) and never changes existing ones.
- `fin.map.normalize.normalize_label`: lower case, apostrophes, ₹/Rs./INR, note references ("(Note 10)"), numbering
  ("(a)", "IV.", "(III - IV)", "(A+B+C)"), "&" to "and", punctuation and whitespace.
- `fin.map.dictionary.load_label_map(root, company)` and `find(label_map, statement, section, label)`.

Notes and deviations
- `label_map` has two keys beyond PLAN §7: `statement` and `section`. Statement tells apart "Profit before tax" in the
  P&L (pbt) and the cash flow (cf_pbt). Section (the heading a line sits under, '' for any) tells apart
  "(a) Financial liabilities - Borrowings" under non-current (lt_borrowings) and current liabilities
  (st_borrowings); the answer key's flat label list cannot express that and lists only one of them. Also `source`,
  `created_at`, `run_id`.
- Statement codes are `balance_sheet`, `profit_and_loss`, `cash_flow`, matching `fin.extract.pdf`.
- Label mappings are seeded from a second CSV (`labels.csv`), since PLAN names only the accounts CSV.
- No foreign keys in the company schema: DuckDB cannot update a referenced row (a superseded document).
- Seed-CSV problems raise E002, like an invalid `config.toml`.

### Phase 3: Synthetic fixtures, Excel ingestion, batch

Added
- The owner's synthetic documents in `tests/fixtures/sample-financials/` (README, `answer_key.json`, `generator/`,
  `clean/`, `broken/`), tracked through `.gitignore` negations; the generator replaces PLAN's `generate.py` and is
  excluded from ruff.
- `fin.extract.excel.extract_workbook`: every value of the balance sheet, P&L and cash flow sheets with sheet and
  cell, statement and scope (from the sheet name and title rows), section heading, label, raw text, period end (header
  dates such as `31.03.2025`, `As at 31-Mar-2025`, Excel dates; any number of year columns) and rupees. Errors: E101
  unreadable, E102 encrypted, E201 no statement sheets, E202 no period header, E203 no unit line, E204 a value that
  is not a number, a value with no label, or a formula with no saved value.
- `fin.extract.units`: unit detection ("₹ in Lakhs", "Rs. in Crores", thousands, millions, rupees) and exact
  conversion to rupees, now shared with `fin.extract.pdf` (its private copy is removed).
- `fin.ingest` and the commands `fin ingest <company> [path] [--force]` (default `inbox/<company>/`) and
  `fin ingest-batch <folder> [--force]` (one sub-folder per company), with `[i/N] company/file: outcome` progress.
  Per file: sha256 dedup (E105, skipped; `--force` loads a new version and marks the old one `superseded`), extract,
  archive the original to `archive/<company>/<sha256>.<ext>`, then one transaction: stage document, raw values and
  facts → run `fin.ingest.VALIDATORS` (empty; Phase 6 adds the gates) → commit, or roll back everything. A failed file
  is copied to `review/<company>/` with `<file>.error.json` (code, message, run_id) and the batch goes on. Unmapped
  labels load with `account` NULL and `confirmed` false and are logged with E301 (counted as pending review).
- PDFs are wired in: `fin ingest` loads them through `fin.extract.pdf.extract_pdf`, stores page and bbox in
  `raw_values`, and writes the auditor's report and notes Markdown to `notes/<company>/` (rows in `notes`). Note-table
  values (related parties, contingent liabilities) stay in the Markdown and are not loaded as facts.
- Resume: every document is its own transaction and duplicates are skipped, so re-running a batch that was killed
  finishes it without duplicates (tested by crashing the second document of a batch).

Verified
- Kestrel (1 workbook, 162 facts) and Bluepeak (3 workbooks, 600 facts) load with every fact equal to
  `answer_key.json` in rupees, at exactly the answer key's sheet!cell, all mapped and confirmed. Tarang (2 PDFs,
  216 facts) matches too, on the expected pages.
- The Kestrel workbook's formula cells have cached values (saved by LibreOffice), so they are read as saved. A
  formula without one raises E204 ("open the file in Excel, save it and ingest it again") rather than recalculating.

Database size vs source size (Windows, DuckDB 2.0.0, clean fixtures through `fin ingest-batch`)

| Company | Source files | Source size | Facts | Database size |
|---|---|---|---|---|
| Kestrel | 1 xlsx | 12,503 B | 162 | 2,633,728 B |
| Bluepeak | 3 xlsx | 39,788 B | 600 | 2,633,728 B |
| Tarang | 2 pdf | 105,756 B | 216 | 4,993,024 B |

Shared files after that run: `dictionary.duckdb` 1,847,296 B, `audit.duckdb` 3,420,160 B. A DuckDB file is mostly
fixed block allocation at this scale (Kestrel and Bluepeak are the same size with 3.7 times the facts), so a company
file starts at a few MB and grows slowly; the archived originals take as much space again as the sources.

Dependencies (exact pins in `uv.lock`)
- `openpyxl` 3.1.5 (MIT): reads `.xlsx` cell values, cached formula results and coordinates (frozen stack). pandas
  is not added; openpyxl alone keeps the extractor simple.

Notes and deviations
- Sources are copied, never moved or deleted: `archive/` and `review/` get copies, and the inbox keeps the original
  (re-running skips it with E105). Clearing the inbox is left to the user.
- An E105 skip does not make the exit code 1, so a resumed batch that only skips finished files exits 0.
- A sheet that does not say standalone or consolidated uses `defaults.scope` from `config.toml`, logged as an event.
- `documents.scope` is `standalone,consolidated` for a workbook with both; each fact has its own scope.
- `documents.fiscal_year` is the latest year in the document. Columns added beyond PLAN §7: `documents.archive_path`,
  `raw_values.statement`, `section`, `rupees_per_unit`, and `facts.doc_id`.
- An invalid company name (anything but lower-case letters, digits, `-`, `_`) is a usage error (exit 2) from the
  command line, before a run starts. In `ingest-batch`, a folder with such a name is skipped with a warning.
- PDF period ends are 31 March of the fiscal year, as `fin.extract.pdf` reads Indian fiscal years.

Human follow-ups
- Ingest one real company workbook and check what the extractor misses (merged cells, wrapped labels, units in the
  header row); real CA files are messier than the fixtures.
- Review the seed label list in `templates/dictionary/labels.csv` and add the wording your documents use.

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

### Phase 5: Mapping and review

Added
- `review/<company>/pending.json`, rewritten after every `fin ingest` / `fin ingest-batch`: one item per unmapped
  label (id, status, statement, section, label, fact count, up to 3 locations, proposal). Ids, proposals and
  rejections survive re-ingest. Written only when a company has unmapped labels.
- `fin map propose <file.json> --company <co>`: stores Claude's proposals (`label`, `account`, `confidence`,
  `reason`, optional `statement`/`section`) as pending. All or nothing; nothing is applied. Unknown account, an
  account of another statement, or an unknown label: E304. Two accounts for one label: E303. Confidence below 0.7
  is logged as E302.
- `fin review`: interactive prompt loop (confirm / reject / edit account / skip / quit; global or company scope)
  without a subcommand, and `fin review list [--json] [--all]`, `confirm <id>... --company X [--account A]
  [--scope global|company]`, `reject <id>... --company X`. Confirm adds the label to `label_map` (source
  `review`), sets `account`, `mapping_id` and `confirmed = true` on the company's matching facts, and removes the
  item. Reject leaves facts unconfirmed. Confirming over an existing mapping with a different account: E303.
- Every propose, confirm, edit and reject appends a `mapping_decisions` row. Actor is `claude` with
  `on_request = true` when `CLAUDECODE` is set, else `human`; proposals are always actor `claude`,
  `on_request = false`.
- Claude Code commands `/ingest`, `/ingest-batch`, `/review`.
- New error E304 (invalid mapping proposal or review request), also in PLAN.md §9.

Dependencies
- None.

Notes and deviations
- Item ids are per company, so `confirm` and `reject` take `--company`.
- `--scope` defaults to `company` (the narrowest). Confirming updates only the reviewed company's facts; another
  company picks up a global mapping at its next ingest (its pending list refreshes then).
- The guard hook already asked before `fin review confirm`; a test with flags was added.
- No audit or company migrations; the dictionary schema is unchanged.

Human follow-ups
- Try `fin review` interactively in a real terminal (tests drive the prompts with piped input).

### Phase 6: Validation gates

Added
- `fin.validate.gates.validate_document`, registered in `fin.ingest.VALIDATORS`. It runs inside each document's
  transaction before COMMIT, for every fiscal year and scope in the document:
  - `balance_sheet` (E401): total assets = total equity and liabilities.
  - `subtotal` (E402): every balance-sheet and P&L subtotal the document shows = the sum of its lines, following
    `dictionary.accounts` (parent, sign). A subtotal the document does not show is replaced by the sum of its lines.
  - `cash_flow` (E403): cash flow closing cash = balance-sheet cash.
  - `cross_document` (E404): each figure = the same company / fiscal year / scope / account in every other loaded
    document (for example a comparative column vs that year's own workbook).
  - `unit_sanity` (E405): a cross-document difference where one figure is exactly a unit factor (a ratio of two
    units fin reads: 10, 100, 1000, ... 10^7) times the other is reported as a unit mistake, not E404. A lone
    document with a wrong unit and no overlap with another document is not caught.
- Tolerances come from `config.toml` (`[tolerances]`); `unit_sanity` uses `cross_document`'s.
- Only facts with a confirmed account take part. A check whose inputs are missing (no total line, unmapped lines in
  the statement, nothing to compare with) does not run and logs a `validate` warning event saying why.
- Every check that runs appends a row to `validation_results` (pass or fail, expected, actual, tolerance). Each
  failure is also an `error` event with its code.
- A failed document is rolled back. `review/<company>/<file>.error.json` gains `checks`: every failed check with
  expected, actual and tolerance. The raised code is the first failed check in the order above.
- The run summary lists failure codes, for example `failed 2 (E401, E404)`.

Notes and deviations
- `fin.ingest.Validator` is now `f(run, company, connection, doc_id)` (was `f(connection, doc_id)`), so gates can
  record results and read config.
- New: `DocumentFailure.details` and `Run.failure_codes`; the audit JSONL now writes `Decimal` values as strings.
- `validation_results.doc_id` of a failed document is the doc_id it had while staged; that row was rolled back, so
  use `file_name` and `run_id` to find it.
- Default tolerances are unchanged at ₹1 lakh per check. The smallest planted defect is ₹12.00 lakhs (Tarang cash
  flow), so every defect is caught, and all clean fixtures pass.
- Cash flow subtotals are not checked (only closing cash vs balance-sheet cash), as the phase asks.

Human follow-ups
- Confirm the ₹1 lakh default tolerances. Documents in crores with two decimals round each line to ₹1 lakh, so a
  long subtotal on a real crore-unit report may need a larger `subtotal` tolerance.

### Phase 7: Metrics and analytics export

Added
- Company migration `002_metrics.sql` (company schema version 2). Plain SQL views, so metrics are always current:
  - `current_facts`: one confirmed, mapped fact per account, fiscal year and scope, from current (`loaded`)
    documents only. When a year appears in more than one document (a comparative column), the document whose own
    fiscal year it is wins; otherwise the latest report, then the latest ingest. Later queries should use it too.
  - `metrics` (long format: fiscal_year, period_end, scope, metric, value DECIMAL(38,6), unit). The metrics are
    revenue_growth, gross_margin, ebitda, ebitda_margin, pat_margin, cfo_to_ebitda, free_cash_flow,
    capex_to_revenue, receivable/inventory/payable days, cash_conversion_cycle, debt_to_ebitda, interest_cover,
    current_ratio, debt_to_equity, roce and roe.
  - Definitions follow the answer key's conventions and are documented in the SQL.
  - Missing inputs or a zero denominator give NULL. Standalone and consolidated are never mixed.
  - `metric_definitions` and `metric_inputs`: lineage from metric to the fact_ids it used. A missing input shows as a
    row with fact_id NULL.
  - Macros `div6` (exact decimal division) and `prior_fy`.
- New database kind `analytics` (`shared/analytics.duckdb`), registered in `fin.db.migrate.targets()`, so `fin init`
  and `fin migrate` create it. It holds tables `metrics` (every company) and `companies` (current documents, facts,
  unconfirmed facts and failed validations per company).
- `fin.metrics.export.refresh_analytics(run)` rebuilds both tables in one short write transaction. It runs after every
  `fin ingest`/`fin ingest-batch`, through `fin metrics refresh`, and after `fin sql --write`
  (`fin.query.write.refresh_metrics` is no longer a placeholder).
- Tests (`tests/integration/test_metrics.py`) ingest every clean fixture and check:
  - every metric equals the answer key for every company, scope and year in it;
  - lineage back to the source cell;
  - unconfirmed and superseded facts are excluded;
  - `fin metrics refresh` writes the expected analytics rows.

Rounding and precision
- Money metrics (ebitda, free_cash_flow) are exact rupees.
- DuckDB divides DECIMALs as DOUBLE, so ratios and days are computed with integer paise arithmetic. They are rounded
  once, half away from zero, to 6 decimal places. Ratios are fractions (0.180000 = 18%).
- The answer key stores unrounded floats, so the test rounds them the same way. The key's ebitda and free_cash_flow
  are in hundredths of a document unit, and the test converts them to rupees.

Deviations
- Bluepeak prints no total current assets or liabilities. When a total is not reported, current_ratio uses the sum
  of the reported current lines, and `metric_inputs` lists those lines (`stands_in_for`). This matches the key.
- First-year revenue_growth (Kestrel and Tarang FY23) is NULL. The key has a value, computed from FY22 data that is
  in no document. The test expects NULL there.
- Not defined by the key, so defined here and documented in the SQL:
  - gross margin = (revenue − cost of materials) / revenue;
  - capex/revenue = −cf_capex / revenue;
  - ROCE = (PBT + finance cost) / (total equity + debt), using closing balances;
  - ROE = PAT / total equity, using closing balances.
- `failed_validations` counts failed rows in audit `validation_results` (all time; the documents themselves were
  rolled back).
- `fin doctor` does not check the analytics schema yet. This was left out to avoid conflicts with Phases 5 and 6, and
  Phase 10 can add it.

### Phase 8: Query and evidence

Added
- `fin query <co> --question "…" --sql "…"`: runs one SELECT (or WITH … SELECT) on a read-only connection and prints
  JSON (query_id, rows, row_count, result_hash). When the result has `fact_id` or `raw_id`, each row gets
  `source_file`, `source_ref` (`sheet!cell` or `page N`) and `source_confirmed`. Anything else (UPDATE, DELETE,
  COPY, ATTACH, several statements) is rejected with E502. Every attempt, accepted or rejected, appends a `queries`
  row (mode 'read'); the query_id is the run_id.
- `fin evidence <query_id>`: re-runs the logged query and writes `evidence/<query_id>.html`: the question, SQL,
  result, and for each fact its source file and reference, with a highlighted PDF crop
  (`evidence/<query_id>/fact_N.png`) or a grid of the Excel cells around the value (two rows above and below, from
  column A) read from the archived workbook, plus flags (unconfirmed mappings, data changed since the query). A
  missing archived source is flagged on the page with E604 (exit 1), never a crash. Appends a `queries` row with
  `evidence_path`.
- `fin sql <co> --write --sql "…"`: backs up the company database to `backups/<run_id>/companies/<co>.duckdb`, runs
  the statement(s) in one transaction (all or nothing), and appends a `queries` row (mode 'write') with the
  statement, rows affected (INSERT/UPDATE/DELETE) and backup path. Without `--write` it refuses with E502 and points
  to `fin query`.
- E504 `SqlFailed`: a `fin query` or `fin sql` statement failed (bad syntax, unknown column); nothing changed.
- Guard hook: `fin sql … --write` gets `ask` (E701).
- `.claude/commands/ask.md`: `/ask` runs `fin query`, then `fin evidence`, and answers in the fixed format (answer,
  calculation, source table with file and sheet!cell or page, SQL, evidence, flags).
- Tests: 10 scripted questions on the three synthetic companies (Excel and PDF), checked against
  `answer_key.json`, each with its evidence page; rejected writes; E504; E604; `fin sql --write` backup, audit row
  and rollback.

Notes and deviations
- New error code E504 (PLAN.md §9), because §9 had no code for a SQL statement that fails to parse or bind.
- Metrics are not refreshed after `fin sql --write` yet, because `fin.metrics` (Phase 7) is not on main. The call
  site is `fin.query.write.refresh_metrics`, which logs a warning event until Phase 7 wires it in.
- Evidence re-runs the query on current data. If the result hash differs from the logged one, the page says so.

### Phase 9: Reports

Added
- `fin report <co> [--scope standalone|consolidated] [--render]` (module `fin.report`). Files live in
  `reports/<co>/<scope>/` in the data folder:
  - First run: writes the narrative stub `narrative.md` (instructions in an HTML comment, the years, metrics and
    accounts available, and one `## ` heading per section), prints `"status": "stub_written"` and stops (exit 0).
  - Next run (or `--render`, which fails with E601 instead of writing a stub): lints the narrative (E602), fills
    `templates/report/health.qmd`, renders `health.html`, `health.docx` and `health.pdf` (Typst) with
    `quarto render --to all`, writes `report_data.json` (every value used, each reference with its text, and the
    data hash), and appends a `reports` audit row (output paths, template commit, data hash, narrative path).
  - Rendering happens in `.render-<run_id>/` next to the outputs; the files are moved into place only when Quarto
    produced all three. A failed render raises E601 and leaves the last good report as it was. No Quarto on PATH is
    E001.
- Template `templates/report/health.qmd` with sections growth, profitability, cash flow quality, working capital,
  leverage and liquidity, earnings quality, observations and data quality (unconfirmed mappings, failed validations,
  missing data), plus an appendix of the facts behind the metrics with file and sheet!cell or page. Python fills
  `{{narrative:<section>}}` and `{{table:<name>}}` placeholders; no code runs inside Quarto, so no Jupyter.
- Every table comes from SQL (`fin.report.data.collect`): the `metrics` view and `current_facts` of the company
  database, and the audit `validation_results` for failed validations (failed documents are rolled back, so they are
  only there). Missing data lists NULL metrics with the inputs `metric_inputs` lacks.
- Narrative references, replaced from the database and formatted in the config display unit:
  `{{metric:<metric>:<FY>}}` (percent for growth, margins, capex/revenue, ROCE and ROE; `x` for the other ratios;
  `days`; money in the display unit) and `{{fact:<account>:<FY>}}`. A NULL value shows as `n/a` and is listed in
  the command's `missing_values`. A reference to an unknown metric, account or year is E601, with line numbers.
- Lint rule (`fin.report.lint`): after removing HTML comments, references and `FYnn` fiscal-year labels, any digit
  on a line (any Unicode digit, including list numbers like `1.`) is a hand-typed number. E602 lists every offending
  line with its number.
- Data hash: SHA-256 of all collected data as sorted JSON (exact decimals as text), so unchanged data gives the same
  hash and the same numbers.
- `.claude/commands/report.md`: `/report` runs the stub, reads the data with `fin query`, writes the narrative
  (describe, don't judge; numbers only as references) and renders.
- CI: new `report` job (ubuntu, Quarto 1.10.19 via `quarto-dev/quarto-actions/setup@v2.2.0`) ingests Kestrel,
  renders the committed sample narrative `tests/fixtures/report/kestrel_narrative.md`, checks the three files exist,
  uploads them as the `kestrel-report` artifact, and runs the report tests with Quarto.
- Tests: `tests/unit/test_report_lint.py` (planted numbers: exact E602 message and line list), and
  `tests/integration/test_report.py` (stub, substituted values, tables, audit row, identical data and hash on
  re-render, a changed fact changes the hash, E602 and E601 keep outputs unchanged, real Quarto render when
  installed). Tests other than the real render use a stand-in `quarto` script on PATH.

Dependencies
- None added. Quarto was already in the Brewfile and `fin doctor`.

Deviations
- Files are under `reports/<co>/<scope>/` rather than `reports/<co>/`, so standalone and consolidated reports of one
  company never overwrite each other.
- The lint allows `FYnn` labels in prose as well as inside references; without them the narrative could not say which
  year a value is for.
- The report has tables, no charts: Quarto renders plain Markdown here (no code execution, so no Jupyter
  dependency).

### Phase 10: Dashboards

Added
- `dashboards/`: an Evidence project (evidence.dev) with a DuckDB source `analytics` over
  `shared/analytics.duckdb` and pages `company` (one company over the years, with a scope picker so standalone and
  consolidated are never mixed), `compare` (companies side by side per metric, scope and year) and `quality`
  (unconfirmed facts and failed validations per company), plus an index of companies. Descriptive only: no scores,
  rankings or recommendations.
- `fin dashboard`: copies the analytics tables to `<data>/dashboard/analytics.duckdb`, runs `npm run sources`, then
  starts the dev server and prints http://localhost:3000. `--build` makes a static build and copies it to
  `<data>/dashboard/build`; a failed sources step or build raises E603 and leaves the last good build in place.
- `install.sh` step 5: `npm ci --prefix dashboards`.
- CI job `dashboard` (ubuntu, Node 22): ingests the clean fixtures, runs `fin dashboard --build` and checks the built
  site has every page and the company names.
- Tests: `tests/integration/test_dashboard.py` (snapshot contents, ingest running while snapshots are taken, build copy,
  E603 keeps the last good build).

Dependencies (npm, in `dashboards/`, all pinned exactly with a committed `package-lock.json`)
- `@evidence-dev/evidence` 40.1.8, `@evidence-dev/duckdb` 2.0.1, `@evidence-dev/core-components` 5.4.2: the dashboard
  framework, its DuckDB source and its charts.
- `typescript` 5.4.2: Evidence peer-depends on exactly this version; without it npm picks TypeScript 7 and fails.

Design notes and deviations
- Locks: Evidence is never pointed at the live analytics file. `fin dashboard` makes a snapshot with one short
  read-only open (through `db/conn.py`), so an ingest waits at most for that copy, and the dev server and build only
  read the snapshot. The snapshot is written with `STORAGE_VERSION 'v1.0.0'` because Evidence bundles an older DuckDB
  (1.4) that cannot read files written by the Python package (2.0).
- Evidence pages are client-side apps, so the built page HTML has no company names; they are in the prerendered query
  results under `api/prerendered_queries`, which is what the CI check reads.

Human follow-ups
- Look at the dashboard in a browser on macOS (`fin dashboard`) with real data.

### Phase 11: Audit, delete, update

Added
- `fin audit <company> --fact <fact_id>` (`--json` for JSON): traces one figure to its file and `sheet!cell` or `page N`,
  with the raw value as written, the dictionary mapping row, every review decision about the label, the archived copy,
  and the ingest run with its events (`fin.audit.trace`). `fin audit <company>` lists the company's history from the audit
  database: runs (ingests, reviews, writes, with their warnings and errors), mapping decisions, queries, reports,
  validation failures and deletions. `fin audit <company> <metric> <fy>` traces a metric through Phase 7's
  `metric_inputs` to its facts (one entry per scope), and each fact to its file and cell or page; an input with no
  fact shows as MISSING.
- `/audit` slash command.
- `fin delete <company>`: needs the company name typed back, or `--yes` (for Claude to use when the user asked; the guard
  hook still asks). It copies everything it removes to `backups/<run_id>/` first, then removes `companies/<co>.duckdb`
  (+ `.wal`), `inbox/`, `archive/`, `review/`, `notes/` and `reports/` of the company, and the evidence pages of its
  queries. It appends a `deletions` row (files removed, row counts, backup path). Audit rows are never touched; mappings
  the company confirmed stay in the shared dictionary. The analytics export is rebuilt afterwards
  (`refresh_analytics`), which drops the company's rows from `shared/analytics.duckdb`. A wrong name exits 3 (E701) and changes nothing.
- `fin update`: refuses if the repo has local changes, backs up every database to `backups/<run_id>/`, then
  `git pull --ff-only`, `uv sync --frozen`, `npm ci --prefix dashboards` (only if `dashboards/package-lock.json`
  exists), `fin migrate` and `fin doctor`. `fin migrate` and `fin doctor` run as new processes (`uv run --frozen fin`),
  so they use the freshly pulled code. `fin metrics refresh` runs between them, because new metric views change the
  analytics export. Each step is an audit event; a failing step stops with E005. A failing
  `fin doctor` ends the update with exit 1.
- `python -m fin` (`src/fin/__main__.py`).
- Error E005 (`UpdateFailed`), added to PLAN.md §9.

Deviations
- PLAN.md §10 says `backups/<timestamp>/`; backups are in `backups/<run_id>/`, like every other backup.

Human follow-ups
- Run `fin update` once on a real clone with Homebrew's uv and node (the test replaces the uv and npm steps).

### Phase 12: Release (documentation)

Added
- `README.md` rewritten for the end user: what the tool does and does not do, install, the daily workflow with the
  slash commands (`/ingest`, `/review`, `/ask`, `/report`, `/audit`) and `fin dashboard`, `fin sql --write`, deleting a company,
  where the data lives, guardrails, `fin update` and uninstall, and a troubleshooting table with every error code.
- `docs/CUSTOMISING.md`: module map, and how to add a dictionary account or label, a metric, a validation gate and a
  report section; how migrations, tests and the sample fixtures work. Linked from `CLAUDE.md`.
- `tests/unit/test_readme.py`: fails if the README troubleshooting table misses an error code defined in
  `fin.audit.errors` (or lists one that does not exist).

Notes
- No git tag is created in this change; `v1.0.0` is added after the clean-machine test (see the `[1.0.0]` heading).
- Phase 10 added `fin dashboard` but no `/dashboard` slash command, so the README gives the CLI command for that step.

Human follow-ups
- Clean-machine test on macOS (new user account, no Homebrew packages assumed): clone the repo, follow only the README
  (`./install.sh`, `claude`, copy a synthetic company from `tests/fixtures/sample-financials/clean/<company>` into
  `~/FinAnalysis/inbox/<company>/`, `/ingest`, `/review`, `/ask`, `/report`, `uv run fin dashboard`, `/audit`), and confirm a first
  report is produced with no outside help. Fix any README step that fails.
- Then tag: `git tag v1.0.0 && git push origin v1.0.0`, and move the Unreleased sections under `## [1.0.0]`.
