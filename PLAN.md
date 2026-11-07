# Financial Health Analysis Toolkit — Build Plan

Working name: `fin-analyst` (repo), `fin` (CLI). Rename freely.

## Phase status

| Phase | Name | Status |
|---|---|---|
| 0 | Repo, install, guardrails | Done |
| 1 | Foundation: config, audit, errors, DB access | Done |
| 2 | Schema and dictionary | Done |
| 3 | Synthetic fixtures, Excel ingestion, batch | Done |
| 4 | PDF bake-off and PDF ingestion | Done (real-PDF bake-off pending; wired into `fin ingest` in Phases 2-3) |
| 5 | Mapping and review | Done |
| 6 | Validation gates | Done |
| 7 | Metrics and analytics export | Done |
| 8 | Query and evidence | Done |
| 9 | Reports | Done |
| 10 | Dashboards | Done |
| 11 | Audit, delete, update | Done |
| 12 | Release | Done, v1.0.0 tagged (Claude Code clean-machine test on macOS pending) |

After 1.0.0 (not phases): real annual-report PDFs, then quarterly and half-year results. Every fact now has a
period (`FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`; see §7). Results statements (SEBI Reg 33) ingest from PDF next to
annual reports, quarter / half-year / year columns of both scopes land in their own periods, and like periods are
compared (E404). Quarterly metrics: revenue, PAT, EBITDA, margins, year-on-year and quarter-on-quarter growth. The
health report stays annual; the dashboard company page shows quarters.

Claude readings (after 1.0.0, not a phase): fin's deterministic extraction stays the primary path (OCR was tested and
rejected). Only when the user asks, Claude Code reads pages fin could not read (scanned, E2xx, skipped statement
pages) as images and writes a reading file (`fin.extract.reading`: one CSV row per figure, as printed, with the
original page or cell). `fin ingest <co> <file> --reading <csv>` loads it for the statements and scopes fin did not
read, through the same stage, gates, commit. Facts carry `facts.read_by` (`fin` / `claude`) and `facts.verified`.
`fin verify <co> <file> --reading <csv>` compares an independent reading with what is loaded (agree / differ /
missing in fin / missing in reading, exact in rupees) and marks agreeing facts verified (a Claude-read fact only by
a reading other than the one it was loaded from, in a document whose gates passed). Every disagreement is a finding,
not a failure: it is shown to the user and recorded in `audit.findings` (check `verify_reading`; the table is
general so other checks can record findings later), verify exits 0 and never changes a figure. Unverified
Claude-read facts still feed metrics; `/ask` flags them and the data quality page counts them.

---

## 1. What it does

Working with many financial data files in Claude Code makes it hard to keep the data maintained, produce outputs and
keep track of everything. `fin-analyst` lets Claude Code turn raw documents into maintained DuckDB databases, query
them with evidence, and produce reports and dashboards. It is built for the owner and a friend, who will customise it
further with Claude Code, so the code and docs stay simple and easy to modify.

- Ingests financial documents (mostly Excel, some PDF) for shortlisted companies into one local DuckDB file per company.
- Answers natural-language questions through Claude Code; every number traced to its source cell or page.
- Generates company health reports (Quarto: HTML, Word, PDF) and dashboards (Evidence, local browser).
- Logs every action; any number, report, or change can be audited.
- Analysis only: no scoring, ranking, or recommendations. Decisions stay human.

## 2. Constraints

- macOS (Apple Silicon and Intel), single user. 16 GB RAM is sufficient.
- Installed from GitHub with one script; updated with one command.
- AI runs through Claude Code only. The Python code never calls an LLM or the Anthropic API.
- Code and data fully separate: updating code never touches data.
- Volume: 2–3 companies a month, plus a one-off backlog of ~5 GB of source files.

## 3. Frozen stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 via uv | Locked deps (`uv.lock`) |
| CLI | Typer | Entry point `fin` |
| Database | DuckDB | One file per company + shared files |
| Excel extraction | openpyxl + pandas | Keeps sheet, cell, type, number format |
| PDF tables | pdfplumber | Chosen by the Phase 4 bake-off (see Phase 4 decision) |
| Notes / auditor text | pdfplumber → Markdown (`fin.extract.text`) | AnyDoc and pdf-inspector are MIT; not needed (Phase 4 decision) |
| Evidence crops | pdfplumber page render + highlight | Avoids AGPL PyMuPDF in an MIT repo |
| Reports | Quarto | HTML, docx, Typst PDF (no LaTeX) |
| Dashboards | Evidence | Node LTS, local dev server |
| Tests / lint | pytest, ruff | |
| CI | GitHub Actions, macOS runner | |
| System deps | Homebrew `Brewfile` | uv, node, quarto |

Pin exact versions in Phase 0. Upgrade deliberately, never implicitly.

## 4. Architecture

Three layers:

1. **`fin` CLI (Python)** — all deterministic work: extraction, loading, validation, metrics, queries, reports, audit. Fully usable without Claude.
2. **Claude Code commands** (`.claude/commands/*.md`) — thin instructions that drive the CLI and do the judgement work: proposing mappings, writing SQL for questions, writing report narrative.
3. **Outputs** — reports, dashboards, evidence pages: files in the data folder.

Where Claude fits:

- Claude reads CLI output (JSON) and writes back only through CLI commands. Data changes go through `fin`, so they are backed up and audit-logged.
- Claude proposes mappings; the human reviews them (`fin review`). Claude may confirm them when the user asks.
- Guardrails prevent accidents only; they never stop Claude doing what the user asked. Risky actions trigger a Claude Code confirmation prompt (PreToolUse guard hook in `.claude/settings.json`, decision `ask`).
- Claude may run any `fin` command, including `fin delete` and `fin review confirm`, when the user asks.

## 5. Repo layout

```
fin-analyst/
  README.md              # install + daily use, written for the end user
  PLAN.md  CLAUDE.md  CHANGELOG.md  LICENSE
  Brewfile  install.sh
  pyproject.toml  uv.lock  .python-version  .gitignore
  .claude/
    settings.json        # hooks
    commands/            # ingest, ingest-batch, review, ask, report, dashboard, audit
    hooks/               # log_action.py, guard.py
  src/fin/
    cli.py  config.py  paths.py  ingest.py
    audit/     logger.py  errors.py
    db/        conn.py  migrate.py  migrations/NNN_*.sql
    extract/   excel.py  pdf.py  text.py  units.py
    map/       dictionary.py  normalize.py  review.py
    validate/  gates.py
    metrics/   views.sql  export.py
    query/     run.py  evidence.py
    report/    render.py  lint.py
  templates/
    report/health.qmd
    dictionary/schedule_iii.csv  labels.csv
  dashboards/            # Evidence project; reads data folder via config
  tests/
    fixtures/sample-financials/ # synthetic companies with known numbers, generator/ and answer_key.json
    unit/  integration/
  .github/workflows/ci.yml
```

## 6. Data folder

Outside the repo. Default `~/FinAnalysis`; override with `FIN_DATA_DIR`.

```
~/FinAnalysis/
  config.toml             # tolerances, display units, defaults
  inbox/<company>/        # drop documents here
  archive/<company>/      # originals after ingest, named by sha256
  companies/<company>.duckdb
  shared/
    dictionary.duckdb
    audit.duckdb          # append-only
    analytics.duckdb      # all companies' metrics, read by dashboards
  review/<company>/       # failed docs + reasons, pending mappings
  notes/<company>/        # Markdown of notes to accounts, auditor reports
  evidence/  reports/  logs/  backups/
```

## 7. Data model

**Per company (`companies/<company>.duckdb`)**

- `documents` — doc_id, file_name, sha256, version, fiscal_year, period_end (latest in the document), scope (standalone/consolidated), status, run_id, superseded_by, ingested_at
- `raw_values` — raw_id, doc_id, sheet, cell, page, bbox, raw_label, raw_value_text, raw_unit, run_id
- `facts` — fact_id, raw_id, account, fiscal_year, period, period_months, period_end, scope, value `DECIMAL(20,2)` in rupees, mapping_id, confirmed, run_id
- `document_facts` and `current_facts` (views): one value per account, period and scope (lines a document maps to the same account are summed, a total line is not added to its parts)
- `notes` — doc_id, section, markdown_path
- `metrics` (views) and `metric_inputs` (metric → fact_ids lineage)
- `schema_version`

**Shared**

- `dictionary.accounts` — account, statement, parent, sign, description, unit (`rupees`, or `rupees_per_share` for EPS: stored as printed, never in a subtotal) (seeded from Schedule III)
- `dictionary.label_map` — normalized label, statement, section (heading the label sits under, or any), account, scope (global/company), company, mapping_id, source
- `audit.*` — see §8
- `analytics.metrics` — export of every company's metrics for dashboards

**Rules**

- All values stored in rupees as exact DECIMAL. Original unit kept in `raw_values`. Display unit set in config.
- Indian fiscal year (Apr–Mar) default; `period_end` stored explicitly.
- Periods: `FY25` is the year ended 31 March 2025 (12 months); `FY26-Q1`..`FY26-Q4` quarters, `FY26-H1`/`FY26-H2`
  half years, `FY26-9M` nine months (`fin.extract.periods`). A balance sheet's period is the part of the year it
  closes (30 Sep 2025 is `FY26-H1`). Quarters, half years and years are never mixed in a metric or a check.
- Standalone and consolidated never mixed in one metric.

## 8. Audit and logging

Every `fin` command:

- Gets a `run_id`; writes a `runs` row at start and end.
- Writes structured events to `audit.events` and to `logs/<date>/<run_id>.jsonl`.
- Ends with a summary: loaded, skipped, failed, pending review, run_id.

**Tables in `shared/audit.duckdb`** (append-only; rows never updated or deleted)

| Table | Records |
|---|---|
| `runs` | run_id, command, args, start, end, status, exit code, git commit, fin/DuckDB/Python versions |
| `events` | run_id, stage, file, level, error code, message, context JSON |
| `mapping_decisions` | raw label → account, actor (dictionary/claude/human), confidence, reason, timestamp |
| `validation_results` | doc_id, check, expected, actual, tolerance, pass/fail |
| `queries` | query_id, question, SQL, row count, result hash, evidence path |
| `reports` | company, output paths, template commit, data hash, narrative path |
| `deletions` | company, files removed, row counts, backup path, timestamp |
| `migrations` | db file, from/to version, backup path, status |

**Claude Code actions**

- `PostToolUse` hook (`log_action.py`): every tool call → `logs/claude_actions.jsonl` (timestamp, session id, tool, command).
- `PreToolUse` hook (`guard.py`) asks the user to confirm, with code E701, before:
  - opening any `.duckdb` file in the data folder except through `fin`
  - `rm`, `mv`, or redirects writing into the data folder
  - `fin review confirm`, `fin delete`

**Traceability**

- `fin audit <company> <metric> <fy>`: metric → facts → mapping decisions → raw values → file + sheet!cell or page, plus the ingest run.
- `fin audit <company>`: full history of ingests, reviews, queries, reports, failures.

## 9. Error handling

| Code | Meaning | Behaviour |
|---|---|---|
| E001 | Dependency missing | `fin doctor` names it and the fix |
| E002 | Dependency version mismatch | Same |
| E003 | Data folder not writable | Stop |
| E004 | Database schema newer than code | Stop; tell user to update |
| E005 | `fin update` stopped (local changes, or a git, uv or npm step failed) | Stop; backup is in `backups/<run_id>/` |
| E101 | File unreadable or corrupt | Skip file, move to review/ with reason |
| E102 | Encrypted file | Same |
| E103 | Unsupported format | Same |
| E104 | Scanned PDF (no text layer) | Same; scanned PDFs out of scope |
| E105 | Duplicate (same sha256) | Skip unless `--force` (creates new version) |
| E201 | No tables found | File to review queue, nothing loaded |
| E202 | Periods/headers not detected | Same |
| E203 | Unit not detected | Same |
| E204 | Parse error | Same |
| E205 | Reading file invalid (`fin ingest --reading`, `fin verify`) | Same; nothing loaded or compared |
| E301 | Unmapped label | Loaded as unconfirmed, queued for review |
| E302 | Low-confidence mapping | Same |
| E303 | Conflicting mapping | Same; human decides |
| E304 | Invalid mapping proposal or review request (unknown account, wrong statement, unknown id or label, invalid new account: name, parent, sign or unit) | Stop; nothing applied |
| E401 | Balance sheet doesn't balance | Whole document rolled back, to review with expected vs actual |
| E402 | Subtotal mismatch | Same |
| E403 | Cash flow doesn't reconcile to cash balance | Same |
| E404 | Same figure differs across documents | Same |
| E405 | Unit sanity check failed | Same |
| E501 | Database locked after retries | Retry with backoff (5 tries), then stop |
| E502 | Write attempted on read-only path | Reject, log |
| E503 | Migration failed | Restore from backup, stop |
| E504 | SQL statement failed (`fin query`, `fin sql`) | Reject, log; nothing changed |
| E601 | Report render failed | Keep last good report |
| E602 | Narrative contains hand-typed numbers | Block render, list offending lines |
| E603 | Dashboard build failed | Keep last good build |
| E604 | Evidence source file missing | Flag in evidence page |
| E701 | Action needs confirmation | Guard asks the user; logged |

**Rules**

- No silent failures, no bare `except`, no default values filling gaps.
- One bad file never stops a batch.
- Each document loads in one transaction: stage → validate → commit, or roll back entirely.
- Tolerances live in `config.toml`, never hard-coded.
- Exit codes: 0 success, 1 completed with failures, 2 command error, 3 refused (needs confirmation).

## 10. Install, update, uninstall

**Install (end user)**

```
git clone <repo-url> && cd fin-analyst
./install.sh
claude
```

`install.sh` (idempotent, logs to `logs/install.log`):

1. Check macOS and architecture.
2. Install Homebrew if missing (asks first).
3. `brew bundle --file=Brewfile` (uv, node, quarto).
4. `uv sync --frozen`
5. `npm ci --prefix dashboards`
6. `uv run fin init` — creates data folder, config, databases at current schema.
7. `uv run fin doctor` — checks every dependency, version, folder permission, schema version.

**Update:** `fin update`

1. Refuse if repo has local changes.
2. Back up all databases to `backups/<timestamp>/`.
3. `git pull`, `uv sync --frozen`, `npm ci`.
4. `fin migrate` (numbered SQL migrations, each in a transaction, logged).
5. `fin doctor`.

**Uninstall:** delete the repo. The data folder is never touched by uninstall.

## 11. Commands

| CLI | Claude Code | Who |
|---|---|---|
| `fin init`, `fin doctor` | — | Human |
| `fin ingest <co> [path]` | `/ingest` | Either |
| `fin ingest <co> <file> --reading <csv>` | `/ingest` (offered after a failure or skipped page) | Claude, when the user asks |
| `fin verify <co> <file> --reading <csv>` | `/verify`, `/ingest` | Either |
| `fin ingest-batch <folder>` | `/ingest-batch` | Either |
| `fin map propose <file.json>` | used inside `/ingest`, `/review` | Claude |
| `fin review` (interactive or flags) | `/review` | Either (asks for confirmation) |
| `fin query --question --sql` | `/ask` | Either |
| `fin sql --write <co> --sql "…"` | `/ask` | Either (on user request; backup + audit) |
| `fin evidence <query_id>` | used inside `/ask` | Either |
| `fin metrics refresh` | runs after ingest | Either |
| `fin report <co>` | `/report` | Either |
| `fin dashboard` | `/dashboard` | Either |
| `fin audit <co> [metric fy]` | `/audit` | Either |
| `fin delete <co>` | — | Either (asks for confirmation) |
| `fin update`, `fin migrate` | — | Human |

## 12. Testing and CI

- **Synthetic fixtures** (`tests/fixtures/sample-financials/`): three fake companies (Kestrel Forgings: Excel with formulas; Bluepeak Polymers: Excel per year, crores, older wording, standalone + consolidated; Tarang Industrial Services: text PDF with notes), known values in `answer_key.json`, plus deliberately broken files (see Phase 6). The set already exists at `sample-financials/` (README, `answer_key.json`, `generator/`, `clean/`, `broken/`), local and untracked; Phase 3 moves it into `tests/fixtures/` and commits the generated files, so CI needs no LibreOffice.
- **Private golden set**: one real company, ~30 hand-checked figures, kept in the data folder; run with `pytest --golden`. Never committed.
- **CI** (macOS runner): ruff, pytest, `shellcheck install.sh`, render report for a synthetic company, build Evidence.
- **Releases**: semantic version tags, `CHANGELOG.md` updated every phase.

---

## 13. Build phases

One phase per Claude Code session. Plan mode first. Don't start the next phase until "done when" passes.

### Phase 0 — Repo, install, guardrails
Build:
- `pyproject.toml` with uv, Typer skeleton (`fin --version`, `fin init`, `fin doctor`)
- `Brewfile`, `install.sh`, `.gitignore`, `LICENSE`, README install section
- `CLAUDE.md`, `.claude/settings.json` with `log_action.py` and `guard.py` hooks
- CI workflow skeleton

Done when:
- Fresh clone → `./install.sh` → `fin doctor` passes
- Running `install.sh` twice causes no changes or errors
- Hook logs a Claude command to `claude_actions.jsonl`
- Guard asks for confirmation before `rm` in the data folder and `fin delete`
- CI green

### Phase 1 — Foundation
Build:
- `config.py`, `paths.py` (data folder resolution)
- Audit logger (runs, events, JSONL), error classes with codes from §9, exit codes
- `db/conn.py`: short-lived connections, read-only by default, retry with backoff on lock
- Migration framework with backup-before-migrate

Done when:
- A test command writes `runs`, `events`, and JSONL
- A forced error logs its code and returns the right exit code
- Lock test retries then raises E501
- `fin migrate` applies, is idempotent, and backs up first

### Phase 2 — Schema and dictionary
Build:
- Migrations for company and shared schemas (§7)
- Schedule III seed CSV and loader
- Label normalization (case, punctuation, "Rs."/"₹", whitespace, note references)

Done when:
- `fin init` creates every database at current schema version
- Synthetic fixture labels map through the dictionary

### Phase 3 — Synthetic fixtures, Excel ingestion, batch
Build:
- Move `sample-financials/` to `tests/fixtures/sample-financials/` and commit the generated files (§12); its `generator/` is the fixture generator
- Excel extractor: sheet/cell capture, header and period detection, unit detection ("₹ in lakhs", "in crores"), multi-year columns
- Staging → commit; dedup by sha256; archive originals
- `fin ingest`, `fin ingest-batch` with progress and resume

Done when:
- Synthetic companies load; every fact traces to a cell
- Re-running skips duplicates
- Killing a batch mid-way and re-running completes with no duplicates
- Database size vs source size measured and recorded in CHANGELOG

### Phase 4 — PDF bake-off and PDF ingestion
Build:
- Bake-off script: pdfplumber vs pdf-inspector on synthetic PDFs + 3–5 real PDFs (local only); score exact-value match; check licenses; record decision and scores in this file
- PDF extractor with page + bbox
- Notes and auditor report sections → Markdown in `notes/`

Done when:
- Winner chosen with scores recorded
- Synthetic PDF values match 100%
- Encrypted → E102, scanned → E104

#### Phase 4 decision

**pdfplumber wins.** Scores from `uv run python scripts/pdf_bakeoff.py` (exact rupee match per label and fiscal
year, against `answer_key.json`; 59 lines × 2 years per report: every statement line plus the related-party and
contingent-liability note rows):

| Report | Table style | pdfplumber | pdf-inspector 1.25.2 |
|---|---|---|---|
| Tarang FY2023-24 | whitespace only, no lines | 118/118 (100.0%) | 110/118 (93.2%) |
| Tarang FY2024-25 | header and total rules | 118/118 (100.0%) | 110/118 (93.2%) |
| **All** | | **236/236 (100.0%)** | **220/236 (93.2%)** |

- pdfplumber is scored through `fin.extract.pdf`, which reads columns from word positions (periods and unit read
  from the page). pdf-inspector is scored on the Markdown tables of `extract_pages_markdown`, and is given what it
  lacks: the key's fiscal years for the value columns, the key's unit, and labels compared ignoring spaces around
  hyphens. Its misses are rows merged with the next section heading ("TOTAL EQUITY AND LIABILITIES II. ASSETS",
  "Net cash from operating activities (A) B. CASH FLOW FROM INVESTING ACTIVITIES"); its balance sheet table also
  dropped the prior-year column header.
- pdfplumber also gives a bbox for every word (needed for evidence crops) and renders pages to PNG through
  pypdfium2, so one library covers tables, notes text and crops. pdf-inspector gives positions for text runs but
  not for Markdown table cells.
- Caveat: the extractor was written against these two synthetic reports, so its 100% is an upper bound. The
  real-PDF run below decides whether the word-position approach holds.
- Licenses (the repo is MIT): pdfplumber MIT, pdfminer.six MIT, pypdfium2 Apache-2.0/BSD-3-Clause, pdf-inspector
  MIT (Firecrawl; Rust wheels for macOS arm64/x86_64), AnyDoc (`firecrawl-anydoc` on PyPI; the `anydoc` name is an
  unrelated Slack bot) MIT. PyMuPDF (AGPL) is not used. pdf-inspector is a dev dependency, for the bake-off only.
- Notes and the auditor's report go to Markdown with pdfplumber too (bold lines become headings, note tables become
  Markdown tables), so AnyDoc is not needed.
- Not done here: real annual reports are not in the repo. Human follow-up: put 3–5 annual reports of small listed
  Indian manufacturers in the private test set, write a key for each in the `answer_key.json` shape, run
  `uv run python scripts/pdf_bakeoff.py <key> --company <name> --show-misses`, and add the scores to this table.
- Real-PDF run (after 1.0.0): six Indian annual reports were tried. The word-position approach holds once header
  blocks, column bands, per-page scope and page skips are in place. Four reports load, and their main totals match the
  PDF for both years. One fails E403 because of a cash mapping, which is correct for that mapping. One is scanned with
  an unusable text layer. Quarterly PDFs fail with E202. Details are in CHANGELOG `[Unreleased]`.

### Phase 5 — Mapping and review
Build:
- Dictionary lookup → `review/<co>/pending.json`
- `fin map propose` accepts Claude JSON proposals (label, account, confidence, reason)
- `fin review`: confirm, reject, edit; global vs company scope. Works interactively and non-interactively (flags such as `--id`, `--account`, `--scope`)
- `/ingest` command: runs ingest, then Claude proposes mappings for pending labels

Done when:
- Unmapped labels flow propose → confirm; dictionary grows
- `fin review confirm` run by Claude asks the user first and records `actor=claude (on user request)` in `mapping_decisions`
- `facts.confirmed` reflects review state

After 1.0.0 (real annual reports): `fin map set` maps a label to an account for one company without a pending item,
which is how a document a gate rejected gets fixed (its `.error.json` lists the lines each failed check used, the
unmapped labels and the commands); then the `review/` copy is ingested again. `fin map accounts` lists the accounts.
Lookup prefers the company's own mappings, and a row under a sub-heading ("Financial liabilities") is also looked up
under the balance-sheet part it is in (from the last part heading, or the last total line). Stored keys are
normalized again when read, so a normalization change does not orphan an existing mapping.

New accounts through review: a proposal may name an account that is not in the dictionary (`new_account`: parent,
sign, unit, description; checked like a seed row, E304 otherwise). `fin review confirm` adds it to `accounts`
(dictionary migration 003: `source` 'seed' or 'review', `run_id`) and maps the label in one transaction; `--name`
and `--parent` change it. `fin map accounts` marks added accounts, and `fin map accounts --added` prints them and the
global labels confirmed in review as seed CSV rows to commit.

### Phase 6 — Validation gates
Build:
- Balance sheet balance, P&L subtotals, cash flow reconciliation, cross-document consistency, unit sanity
- Tolerances from config; failed document rolled back to `review/` with expected vs actual

Done when:
- Each broken fixture fails with its expected error and nothing is loaded:

  | File | Expected error | Defect |
  |---|---|---|
  | `Kestrel_UNBALANCED_balance_sheet.xlsx` | E401 | FY25 assets ₹50.00 lakhs above liabilities |
  | `Kestrel_SUBTOTAL_mismatch.xlsx` | E402 | FY24 total expenses ₹12.34 lakhs above the sum of its lines |
  | `Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx` | E203 | Unit line removed |
  | `Bluepeak_Polymers_FY2024-25_COMPARATIVE_mismatch.xlsx` | E404 | FY24 comparatives differ from the FY24 workbook by ₹3.50 Cr; subtotals still add up |
  | `Tarang_FY2024-25_ENCRYPTED.pdf` | E102 | Password protected (`test1234`) |
  | `Tarang_FY2024-25_SCANNED.pdf` | E104 | Image only, no text layer |
  | `Tarang_FY2024-25_CASHFLOW_not_reconciling.pdf` | E403 | Cash flow closing cash ₹12.00 lakhs above balance sheet cash |
  | Any clean file ingested twice | E105 | Duplicate |
- Clean fixtures pass

After 1.0.0 (real annual reports): the subtotal tree covers total liabilities, profit before exceptional and
extraordinary items, total tax, continuing and discontinued operations, OCI and total comprehensive income. Unmapped
nil lines, per-share lines and P&L "attributable to" lines do not stop E402. Exceptional and extraordinary items are
stored as printed; E402 tries them subtracted (Schedule III) and then added (a gain printed as positive), and says
which. Old-format "Cash and bank balances" can be mapped to `cash_and_bank_balances`, which E403 does not compare with
closing cash.

### Phase 7 — Metrics and analytics export
Build:
- SQL views: revenue growth; gross, EBITDA, PAT margins; CFO/EBITDA; free cash flow; capex/revenue; receivable, inventory, payable days; cash conversion cycle; debt/EBITDA; interest cover; current ratio; debt/equity; ROCE; ROE
- `metric_inputs` lineage
- Export to `analytics.duckdb` after every ingest

Done when:
- Synthetic metrics equal precomputed expected values exactly

### Phase 8 — Query and evidence
Build:
- `fin query`: read-only, rejects non-SELECT (E502), returns JSON with provenance columns, logs to `queries`
- `fin sql --write <co> --sql "…"`: applies a user-requested data change to one company DB. Backs up first, runs in one transaction, records the statement and rows affected in the audit log, and refreshes metrics. `fin query` stays read-only
- `fin evidence <query_id>`: HTML page with highlighted PDF crops and Excel context grids
- `/ask` command with fixed answer format: answer, calculation, source table, SQL, flags

Done when:
- 10 scripted questions on synthetic data answer correctly, each with a working evidence page
- A write attempt is rejected and logged

### Phase 9 — Reports
Build:
- `templates/report/health.qmd` (sections: growth, profitability, cash flow quality, working capital, leverage and liquidity, earnings quality, observations, data quality)
- `fin report <co>`: creates narrative stub → `/report` has Claude fill it → lint (E602) → render HTML, docx, PDF → log with data hash

Done when:
- Synthetic company renders in all three formats
- Lint catches a planted hand-typed number
- Re-rendering with unchanged data gives identical numbers and data hash

### Phase 10 — Dashboards
Build:
- Evidence project, DuckDB source pointing at `analytics.duckdb` via config
- Pages: company, compare, data quality (unconfirmed mappings, failed validations)
- `fin dashboard`: refresh sources, start dev server

Done when:
- Dashboard shows synthetic companies
- Running an ingest while the dashboard is open causes no lock error

### Phase 11 — Audit, delete, update
Build:
- `fin audit` trace (§8)
- `fin delete`: interactive typed company-name confirmation, or `--yes` for non-interactive use after the user asked. Backup first, then a deletion record
- `fin update` (§10)

Done when:
- A figure in a report traces back to its cell or page
- After delete, company file is gone and audit trail remains
- Update on a test clone runs migrations with backup

### Phase 12 — Release
Build:
- README for the end user: install, daily workflow, troubleshooting by error code
- Clean-machine test: new macOS user account, clone → first report using README only
- Tag `v1.0.0`

Done when:
- Clean-machine test passes without help

---

## 14. Session prompt (paste at the start of each phase)

```
Read CLAUDE.md and PLAN.md. We are doing Phase <N>: <name>.

1. Enter plan mode. Propose the implementation for this phase only:
   files, functions, tests, and how you will verify each "done when" item.
2. Wait for my approval.
3. Implement with tests. Use the error codes and audit logger from
   src/fin/audit. No new dependency without telling me why.
4. Run ruff and the full test suite, then demonstrate each "done when"
   item. Show me the output.
5. Update CHANGELOG.md and the phase status table in PLAN.md.
   Commit. Do not start the next phase.
```
