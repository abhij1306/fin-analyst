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
