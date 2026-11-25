# Product spec

What fin does and how it behaves: constraints, stack, architecture, data model, audit, errors, install, commands and testing (sections 1 to 12), then the behaviour added after 1.0.0 in detail. Diligence: [diligence.md](diligence.md). Extending fin: [development.md](development.md). Error codes: [errors.md](errors.md). Product-level decisions: [decisions/](decisions/).

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
| PDF tables | pdfplumber | Chosen by a bake-off against other PDF table readers |
| PDF fallback (optional) | One of Docling 2.148, LiteParse 2.21 or LlamaParse (`llama-cloud` 2.20), as uv extras | Pages pdfplumber skips, and scanned results filings (OCR); opt-in, `fin parser use <name>` |
| Notes / auditor text | pdfplumber → Markdown (`fin.extract.text`) | AnyDoc and pdf-inspector are MIT; not needed |
| Evidence crops | pdfplumber page render + highlight | One library for text, tables and crops |
| Reports | Quarto | HTML, docx, Typst PDF (no LaTeX) |
| Dashboards | Evidence | Node LTS, local dev server |
| Tests / lint | pytest, ruff | |
| CI | GitHub Actions | Linux on every push; macOS on main and on demand |
| System deps | Homebrew `Brewfile` | uv, node, quarto, poppler |

Python dependencies are pinned exactly in `uv.lock`. Homebrew cannot pin versions, so `install.sh` takes its current
uv, node, quarto and poppler, and `fin doctor` checks minimum versions for uv, node and quarto. CI pins the tested
Node and Quarto versions. Upgrade deliberately, never implicitly.

## 4. Architecture

Three layers:

1. **`fin` CLI (Python)** — all deterministic work: extraction, loading, validation, metrics, queries, reports, audit. Fully usable without Claude.
2. **Claude Code commands** (`.claude/commands/*.md`) — thin instructions that drive the CLI and do the judgement work: proposing mappings, writing SQL for questions, writing report narrative.
3. **Outputs** — reports, dashboards, evidence pages: files in the data folder.

Where Claude fits:

- Claude reads CLI output (JSON) and writes back only through CLI commands. Data changes go through `fin`, so they are backed up and audit-logged.
- Claude proposes mappings; the human reviews them (`/review`). Claude may confirm them when the user asks.
- Guardrails prevent accidents only; they never stop Claude doing what the user asked. No approval prompts on Claude: data changes go through `fin`, which backs up and audit-logs every one.
- Conflicting data in what a company filed is a finding to show, never a failure: the document is held until the user accepts it as filed or has Claude fix it.
- Claude may run any `fin` command, including `fin delete` and `fin review confirm`, when the user asks.

## 5. Repo layout

```
fin-analyst/
  README.md              # install + daily use, written for the end user
  CLAUDE.md  CHANGELOG.md  LICENSE
  docs/                  # product.md, diligence.md, development.md, errors.md, client-handoff.md, decisions/
  Brewfile  install.sh
  pyproject.toml  uv.lock  .python-version  .gitignore
  .claude/
    settings.json        # hooks
    commands/            # ingest, ingest-batch, review, ask, statements, report, dashboard, audit, status, verify
    hooks/               # log_action.py, gate_guard.py
  src/fin/
    cli/  config.py  paths.py  ingest.py  statements.py  workbook.py
    audit/     logger.py  errors.py
    db/        conn.py  schema.py  schema/*.sql (SCHEMA_FILES in schema.py)
    extract/   excel.py  pdf.py  pdf_model.py  pdf_lines.py  pdf_columns.py  pdf_reader.py  pdf_tables.py  addon.py  fallback.py  docling_run.py  liteparse_run.py
               llamaparse_run.py  text.py  units.py  periods.py  reading.py
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
  .github/workflows/      # ci.yml (Linux), macos.yml
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
  evals/<company>/        # golden set and eval reports; evals/bundle-* results bundles (docs/client-handoff.md)
  notes/<company>/<sha256[:12]>/  # Markdown of notes to accounts, auditor reports (one folder per PDF: two PDFs with one name never overwrite each other)
  evidence/  reports/  logs/  backups/
```

## 7. Data model

**Per company (`companies/<company>.duckdb`)**

- `documents` — doc_id, file_name, sha256, version, fiscal_year, period_end (latest in the document), scope (standalone/consolidated), status, run_id, superseded_by, ingested_at
- `raw_values` — raw_id, doc_id, sheet, cell, page, bbox, raw_label, raw_value_text, raw_unit, run_id
- `facts` — fact_id, raw_id, account, fiscal_year, period, period_months, period_end, scope, value `DECIMAL(20,2)` in rupees, mapping_id, confirmed, run_id
- `document_facts` and `current_facts` (views): one value per account, period and scope (lines a document maps to the same account are summed, a total line is not added to its parts; nil lines are left out of this test; of just two equal lines, one is the total only when it is labelled "Total..." or the other sits under it)
- `notes` — doc_id, section, markdown_path
- `metrics`, `metric_definitions`, `metric_input_accounts` (views) and `metric_inputs` (metric → fact_ids lineage)
- `standard_statements` (temporary view per connection, `fin.statements`): the standard P&L, balance sheet and cash flow with coverage
- Diligence: `engagement`, `accountant_queries`, `adjustments`, `passthrough_customers` (tables) and `engagement_periods`, `adjusted_ebitda`, `ebitda_bridge`, `ebitda_to_cash`, `revenue_split`, `passthrough_lines`, `customer_revenue`, `customer_concentration`, `customer_retention`, `customer_cohorts`, `sector_mix`, `product_revenue`, `product_pvm`, `product_pvm_summary` (views on the latest engagement; `customers` is a table)
- Diligence source files (`sources.sql`): `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements`, `bank_lines`, `mask_values` (never printed: `fin sql` refuses it, E810), `ledger_accounts`, `recon_runs`, `recon_items`, `payroll_lines`, `payroll_units`, the view `stub_figures`

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
- Writes structured events to `audit.events` and to `logs/<date>/<run_id>.jsonl`. If writing the log fails (E003),
  it is not retried for that run; the run's exit code reflects it, and the end row is written after everything else, so
  it records the exit code the command returns. The one limit: when writing the end row itself fails, the exit code
  rises after the row was made, so a copy of it written to the other log records the lower code; fin then prints both
  codes on stderr.
- Ends with a summary: loaded, skipped, failed, pending review, held, run_id.

**Tables in `shared/audit.duckdb`** (append-only; rows never updated or deleted)

| Table | Records |
|---|---|
| `runs` | run_id, command, args, start, end, status, exit code, git commit, fin/DuckDB/Python versions |
| `events` | run_id, stage, file, level, error code, message, context JSON |
| `mapping_decisions` | raw label → account, actor (dictionary/claude/human), confidence, reason, timestamp |
| `validation_results` | doc_id, check, expected, actual, tolerance, pass/fail |
| `findings` | every finding and every status change (open, accepted, resolved, superseded): check, code, both values and sources |
| `queries` | query_id, question, SQL, row count, result hash, evidence path |
| `reports` | company, output paths, template commit, data hash, narrative path |
| `deletions` | company, files removed, row counts, backup path, timestamp |
| `diligence_events` | every engagement, accountant query and adjustment added, every status change, and every gate approval: kind, id (a gate's name), status, the row as JSON |

**Claude Code actions**

- `PostToolUse` hook (`log_action.py`): every tool call → `logs/claude_actions.jsonl` (timestamp, session id, tool, command).
- One approval prompt: the `PreToolUse` hook `gate_guard.py` (matcher `Bash`) asks the user before any command
  that looks like a gate approval, since only the user approves a diligence gate. It normalises the command (quotes
  and backslashes removed, whitespace collapsed, lower case) and asks when it has the word `gate` followed later by
  `approve`, or names `gate_approvals`, `approve_in`, `gates.approve` or `from fin.diligence.gates import`; when in
  doubt it asks. It is a guard for an agent that follows CLAUDE.md, not enforcement: other ways to approve are not
  matched, and the controls are E801 (a command that needs a gate refuses until it is approved) and E802 (a gate with a
  failing checklist cannot be approved). It prints nothing for any other command or for input it cannot read, so it never blocks anything
  else. `fin sql --write` cannot write gate approvals (E806, below). Every other data change goes through `fin`, which
  backs up and records it, without a prompt (the general guard hook was removed on 2026-11-07).

**Traceability**

- `fin audit <company> <metric> <fy>`: metric → facts → mapping decisions → raw values → file + sheet!cell or page, plus the ingest run.
- `fin audit <company>`: full history of ingests, reviews, queries, reports, failures and diligence changes (every query, adjustment, gate and engagement change, with its run id). A run belongs to the company
  when its args name it or an audit row names it (events about `<company>/<file>`, mapping decisions, validation
  results, findings, queries, reports, deletions, diligence events), so a batch ingest stays after the company is
  deleted.
- `fin delete <company>` backs up, then removes the company's database, inbox, archive, review, notes, reports, exports and evals folders, the evidence pages of its queries and the saved PDF parses of its files (`cache/<engine>/<sha256>...`, except those of a file another company loaded too); it prints how many company-scope label mappings stay in the shared dictionary.
- Lineage of a summed value: `current_facts.fact_ids` (and `standard_statements.fact_ids`) list every printed line
  behind it. `fin sql` with a `fact_ids` column gives every line's source ("sum of N sources: ..."), confirmed only
  when every line is; the evidence page shows each line. Traces show a per-share fact in rupees per
  share.

## 9. Error handling

The code table (meaning and what to do) is in `docs/errors.md`; each code's class
is in `src/fin/audit/errors.py`. E1xx-E2xx fail one document (the batch goes on); E301-E303 load facts unconfirmed;
E401-E405 are findings (the document is held); setup, database and output errors stop the command.

**Rules**

- No silent failures, no bare `except`, no default values filling gaps.
- One bad file never stops a batch: a figure too large to store (E890) or rows the database refuses (E891) fail that file only, which goes to `review/` with the reason. A first ingest for a new company where no file loads leaves no company database behind.
- Each document loads in one transaction: stage → validate → commit (held when a check finds something), or roll
  back entirely on an error.
- What a company filed that does not add up is a finding, never a failure. Only nothing readable (E1xx/E2xx) fails.
- Tolerances live in `config.toml`, never hard-coded.
- Exit codes: 0 success, 1 completed with failures, 2 command error, 3 refused (needs confirmation).

## 10. Install, update, uninstall

**Install (end user)**

```
git clone <repo-url> && cd fin-analyst
./install.sh             # or ./install.sh --parser docling|liteparse|llamaparse for the optional PDF add-on
claude
```

`install.sh` (idempotent, logs to `logs/install.log`):

1. Check macOS and architecture.
2. Install Homebrew if missing (asks first).
3. `brew bundle --file=Brewfile` (uv, node, quarto, poppler); tried up to 3 times, a minute apart, since GitHub
   release downloads sometimes fail for a minute or two.
4. `uv sync --frozen` (with `--extra <name>` for the PDF add-on `--parser <name>` names, or `config.toml`'s
   `[pdf] parser` without the flag; `--parser none` removes it).
5. `npm ci --prefix dashboards`
6. `uv run fin init` — creates data folder, config, databases from the schema files; then, with `--parser` or an
   add-on in `config.toml`, `uv run fin parser use <name>` downloads its models and records it.
7. `uv run fin doctor` — checks every dependency, version, folder permission, that the databases exist and are not from an older fin.

**Update:** `fin update`

1. Refuse if repo has local changes.
2. Back up all databases to `backups/<run_id>/`.
3. `git pull`, `uv sync --frozen` (with `--extra <name>` for the add-on `[pdf] parser` names), `npm ci`.
4. Refresh the metrics export.
5. `fin doctor`. If it fails, `fin update` prints each failed check (code and fix), or the last lines of the doctor's output when it stopped before listing any (an error code, a crash), and the doctor's own run id, with the update's run id.

**Uninstall:** delete the repo. The data folder is never touched by uninstall.

## 11. Commands

| CLI | Claude Code | Who |
|---|---|---|
| `fin init`, `fin doctor` | — | Human |
| `fin ingest <co> [path] [--force]` | `/ingest` | Either |
| `fin ingest <co> <file> --reading <csv>` | `/ingest` (offered after a failure or skipped page) | Claude, when the user asks |
| `fin ingest <co> <file> --kind trial_balance\|ledger_vouchers\|registers\|payroll\|bank [--profile]` | `/diligence` | Either |
| `fin payroll confirm-unit <co> <file> --annual\|--monthly` | `/diligence` | Either (only when the user says which) |
| `fin ingest-batch [folder]` (default `inbox/`) | `/ingest-batch` | Either |
| `fin verify <co> <file> --reading <csv>` | `/verify` | Either (when the user asks) |
| `fin map propose <co> <file.json>` | used inside `/ingest` | Claude |
| `fin map set <co> --statement --label --account`, `fin map accounts` | `/ingest` (fixing a finding) | Either |
| `fin review list [co]`, `fin review confirm\|reject <co> <ids>` | `/review` | Either (confirm when the user says) |
| `fin findings list [co]`, `fin findings accept <co> <ids>` | `/ingest`, `/verify` | Either (accept when the user says save as is) |
| `fin status [co]` | `/status` | Either |
| `fin sql <co> --sql "…" [--question] [--evidence]` | `/ask`, `/report` | Either (reads) |
| `fin sql <co> --write --sql "…"` | — | Either (on user request; backup + audit) |
| `fin statements <co> [--period] [--scope] [--json]`, `fin export <co>` | `/statements` | Either |
| `fin report <co> [--scope] [--peers] [--render]` | `/report` | Either |
| `fin dashboard [--build]` | `/dashboard` | Either |
| `fin audit <co> [metric period \| --fact id]` | `/audit` | Either |
| `fin delete <co> [--yes]` | — | Either (typed confirmation, or `--yes` when the user asked) |
| `fin update` | — | Human |
| `fin engagement open <co> --periods FY24,FY25,FY26 [--threshold]`, `fin engagement status\|close <co>` | `/diligence` | Either (`close` needs gate G4 approved) |
| `fin engagement bridge <co> [--period] [--scope] [--json]` (views `adjusted_ebitda`, `ebitda_bridge`, `ebitda_to_cash`) | `/diligence` | Either |
| `fin query list\|raise\|edit <co>` | `/diligence` | Claude drafts |
| `fin query approve\|sent\|answer\|close\|withdraw <co> <ids>` | `/diligence` | Either (when the user says; `sent` only once the user sent it) |
| `fin engagement passthrough <co> [--customer ... --source] [--clear]` | `/diligence` | The user's list; Claude records it when given |
| `fin map customers <co> [file]`, `fin review list\|confirm\|reject <co> --customers` | `/diligence` | Claude proposes sectors; the user decides |
| `fin analytics <co> [--json]` | `/diligence` | Either |
| `fin recast run <co> [--period]`, `fin recast contracts <co> [<file> --reading <csv>]` | `/diligence` | Claude runs it; the user accepts or rejects the proposed adjustments |
| `fin contracts coverage <co> [--json]`, `fin sample draw <co> --kind sales\|purchase --period P [--n] [--seed]`, `fin sample show <co> [id]`, `fin walkthrough record <co> <sample-id> <file.json>`, `fin walkthrough summary <co>` | `/diligence` | Claude runs them; a walkthrough exception at or above the threshold is grouped into a query the user approves |
| `fin query raise <co> --topic T --question "..."`, `fin query checklist <co>` (FDD F2) | `/diligence` | Claude drafts inquiries; the user approves |
| `fin provisions show\|ledgers\|propose <co>` (FDD F3) | `/diligence` | Claude proposes provision ledgers |
| `fin provisions confirm\|reject <co> <ids>` | `/diligence` | Either (when the user says) |
| `fin adjust propose\|list <co>` | `/diligence` | Claude proposes |
| `fin adjust accept\|reject <co> <ids>` | `/diligence` | Either (when the user says; needs gate G2 approved) |
| `fin gate status <co> [--json]` | `/diligence` | Either |
| `fin gate approve <co> <gate>` | `/diligence` | Human (Claude only when the user says; the PreToolUse hook asks) |
| `fin eval init\|run <co>`, `fin eval bundle [co] [--with-figures]` | `/eval` | Either (only a person marks a golden line checked) |

Metrics are views in each company database; `shared/analytics.duckdb` is rebuilt after every ingest, data change,
verify and update.

## 12. Testing and CI

- **Synthetic fixtures** (`tests/fixtures/sample-financials/`): three fake companies (Kestrel Forgings: Excel with formulas; Bluepeak Polymers: Excel per year, crores, older wording, standalone + consolidated; Tarang Industrial Services: text PDF with notes), known values in `answer_key.json`, plus deliberately broken files. The set is committed, so CI needs no LibreOffice.
- **Golden evaluation** (`fin.evals`, runbook `docs/client-handoff.md`): per company, `evals/<company>/golden.csv` in the data folder (never committed) holds values a person checked in the real documents, with their source. `fin eval init <co>` lists what to check (statement figures in current figures, trial balance ledgers, `tb_vs_statements` lines, stub figures, bank closing balances, register totals per sheet and payroll totals) with source and unit and leaves the value and `checked` empty; only lines marked `checked` are scored. `fin eval run <co>` compares each exactly in rupees after unit conversion (pass, fail, missing in fin, unchecked; the relative difference to six significant digits, so a misread digit in a large figure never shows as 0) and writes a report to `evals/<co>/`, with counts: error codes in the company's runs, findings by code, unread pages, Claude-read figures by document status and latest verify outcome, unmapped labels, mapping items waiting, ledgers by status, bank statements by reader and tie, register lines by kind and payroll files by confirmed unit. `fin eval bundle [co] [--with-figures]` writes a shareable Markdown and JSON file (fin version and commit, doctor summary, scores, lines not passing with their source kind and related codes, counts): companies as `company-N`, ledgers only as mask tokens, no file names, labels or sources, no absolute figures without `--with-figures`; E833 refuses one that would contain a masked, company or file name. Errors E830-E833. Decisions: `docs/decisions/eval-kit.md`.
- **CI** (`.github/workflows/ci.yml`, Linux, every PR and main push): ruff, the file-size check (`tools/check_file_size.mjs`: source files under 6,000 tokens), pytest, `shellcheck install.sh`; one job ingests the synthetic companies once, renders a report (Quarto) and builds the Evidence dashboard (node 24). A `pdf-addon` job per engine syncs its extra: Docling and LiteParse restore their model caches and
run the tests marked `docling` / `liteparse` (the real engine on synthetic PDFs); LlamaParse checks its fixture's
shape against the SDK and never calls the API (its live test skips without `LLAMA_CLOUD_API_KEY`); the other jobs run
without the extras. `macos.yml` (main and on demand): `install.sh` twice, `fin doctor` and a smoke ingest; and, for
Docling and LiteParse, `install.sh --parser <name>`, the real-engine tests and `--parser none`.
- **Releases**: semantic version tags, `CHANGELOG.md` written at release.

---

## Behaviour added after 1.0.0

After 1.0.0 (not phases): real annual-report PDFs, then quarterly and half-year results. Every fact now has a
period (`FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`; see §7). Results statements (SEBI Reg 33) ingest from PDF next to
annual reports, quarter / half-year / year columns land in their own periods, and like periods are compared
(E404). Quarterly metrics: revenue, PAT, EBITDA, margins, year-on-year and quarter-on-quarter growth. The
health report is annual, with a short "latest quarters" section; the dashboard company page shows quarters.

Claude readings (after 1.0.0, not a phase): fin's deterministic extraction stays the primary path; the optional
PDF add-on (below) is the automatic fallback for PDF pages pdfplumber skips. Only when the user asks, Claude Code
reads pages neither could read (scanned, E2xx, skipped statement pages) as images and writes a reading file (`fin.extract.reading`: one CSV row per figure, as printed, with the
original page or cell). `fin ingest <co> <file> --reading <csv>` loads it for the statements and scopes neither fin nor
the PDF add-on read, through the same stage, gates, commit. Facts carry `facts.read_by` (`fin`; `docling`,
`liteparse` or `llamaparse`; `claude`), as
information: a Claude-read figure is not "unverified" (owner decision 2026-11-09); whether it is trusted is the
user's decision.
`fin verify <co> <file> --reading <csv>` is an on-demand check the user runs from `/verify` whenever they want, on
any loaded or held document, any number of times: Claude reads the document again and fin compares that reading
with what is stored (agree / differ / missing in fin / missing in reading). Any reading can be compared, the one the
document was loaded with included. Each run on a loaded or held document is recorded in the company database:
`verify_runs` (run, document, reading, the count of each outcome) and `verify_lines` (every line compared, both
sides); this history replaces the former `facts.verified` flag (a company database made before it lacks the two
tables and reports E002: re-create the data folder, which the owner accepted on 2026-11-09, or run their `CREATE TABLE`
statements from `company.sql`). It compares the value fin uses now (`facts.value`, so a figure corrected with
`fin sql --write` is checked as corrected) with the reading's figure converted to rupees with the reading's own unit
(EPS as printed), exactly. It works on loaded and held documents alike (on a held document, a reading tells whether
a finding is a misread). Every disagreement is a finding, not a failure: it is shown to the user, kept with the
document in the company `findings` table and recorded in `audit.findings` (check `verify_reading`), and it neither
holds nor releases the document: a loaded document stays loaded, a held one stays held. The latest run wins
(owner decision 2026-11-09): a verify finding stays open until the user accepts it or a later run settles it. A run
resolves the open findings of its own reading that it no longer finds, and every open verify finding of the
document on a loaded line (differ, missing in reading: the finding's `fact_id`) that it agrees on, whatever reading
that finding came from; the earlier disagreement stays in `verify_lines`. A missing-in-fin finding has no loaded
line to agree on, so only a run of its own reading resolves it; accepted findings stay accepted. Verify exits 0, never
changes a figure, and refreshes `shared/analytics.duckdb` so its findings show on the data quality page.
Facts read by Claude or the PDF add-on feed metrics like any other; `/ask` says "read by Claude" (or Docling,
LiteParse, LlamaParse) next to their source, as information, and `fin status` counts them.

PDF add-on (after 1.0.0, not a phase; owner decisions 2026-11-07, Docling, and 2026-11-24, the choice of three): an
opt-in add-on, not a required dependency, and at most one engine at a time: `[pdf] parser` in `config.toml` is
`"none"` (the default, also when the file has no `[pdf]` table), `"docling"`, `"liteparse"` or `"llamaparse"`.
`fin parser use <name>` (`/parser`; `./install.sh --parser <name>` runs it)
syncs that uv extra (`uv sync --frozen --extra <name>`; `none`: a plain sync, which removes every add-on), downloads
the engine's models when missing, writes `[pdf] parser`, and prints what changed; `fin parser show` and the `pdf
parser` line of `fin doctor` show the choice, whether it imports, its models, and for LlamaParse whether
`LLAMA_CLOUD_API_KEY` is set. A second `install.sh` and `fin update` keep what `config.toml` says. The engines:
- Docling (`docling` extra: Docling 2.148, PyTorch, about 1.4 GB with its models in `~/.cache/docling/models`; not on
  Intel Macs, where PyTorch has no wheels): local; layout, table structure and OCR, one converter per process. Chosen
  from a benchmark on real NSE filings: on 19 pages pdfplumber skipped, it found 99-100% of the figures of Claude's
  readings, at about 15 s a page on CPU.
- LiteParse (`liteparse` extra, Apache-2.0; its `ocria-nano` model package, about 50 MB, under its own model licence):
  local and fast. It reads the pages' text layer and finds tables with its learned table model; pages with a text
  layer are never OCR'd (plain Tesseract misread 11-58% of numbers on digital pages), and fin never uses its OCR: on
  a scanned test report ocria-nano dropped the brackets of a negative figure, so a scanned PDF stays E104 when LiteParse
  is chosen. A header line the model gives as a shorter row is placed under the columns whose span its cells' boxes
  cover at least half of (LiteParse's own positions; a cell that fits no column leaves the page unread, E204); the
  adapter adds no layout rule, so a page whose grid fin's reader refuses stays unread.
- LlamaParse (`llamaparse` extra, the `llama-cloud` SDK): LlamaIndex's cloud parser (tier `agentic`), paid per page.
  Only the skipped pages are uploaded, as one PDF named `pages.pdf`; the API key is read only from the environment
  variable `LLAMA_CLOUD_API_KEY` (never from or into the repo, `config.toml` or the data folder, never logged); its
  response is saved in `cache/llamaparse/<sha256>-p<pages>.json` in the data folder, so ingesting the file again
  does not call the API. Tables come from its structured items (rows of cells, one box per table).
The pipeline for every PDF is: pdfplumber (`fin.extract.pdf`, unchanged) -> the chosen engine on only the statement
pages pdfplumber skipped (E201-E204) -> anything still unread stays unread. `fin.extract.fallback` picks the engine;
`fin.extract.addon` runs it once per document on a PDF of just those pages; each engine's adapter
(`fin.extract.docling_run`, `liteparse_run`, `llamaparse_run`) gives `pdf_tables.Table` grids with boxes in fin's
top-left points, and `fin.extract.pdf_tables` reads them with fin's own rules and helpers, the same for every engine:
header rows give each column's period end, length and scope ("Standalone" / "Consolidated" spanning the columns,
else the page title), the label is the text columns left of the first period column (a "Note" column kept as the
note), headings are sections, the unit is the page's unit line, and the same guards apply (two columns with the same
period and scope: E202; no unit: E203; misread figures or a row shorter than its table: E204; no usable table: E201). Evidence crops work as for
pdfplumber. Facts it reads have `read_by` = the engine's name; the outcome and log say how many figures it read and
from which pages. When no page could be read by pdfplumber, the add-on still tries before the file fails. A scanned
PDF (E104) of at most 40 pages (a results filing) is OCR'd by Docling or LlamaParse and its statement pages found by
fin's title rules; a longer scanned PDF, or any scanned PDF with LiteParse chosen, stays E104. The engine failing, its models failing to download, or LlamaParse without
its key is a warning (E206, naming the engine) and leaves the pages unread; it never fails a document. With no
add-on chosen, or the chosen one not installed, pages stay unread and the run logs one notice per document naming
them and the two ways to read them (`fin parser use <name>`, or a Claude reading). With a reading, the order is
fin -> the add-on -> the reading fills the statements and scopes neither read.

Findings instead of failures (after 1.0.0, not a phase; owner decision 2026-11-07): fin is an analysis tool, not an
opinionated one. Conflicting data in what a company filed (balance sheet not tying, subtotals not adding up, cash
not reconciling, a figure differing between documents such as a restatement or a quarter vs its year, fin vs a
Claude reading) is a finding to show, never a failure that rejects data. A fin-vs-reading finding (`fin verify`,
above) is kept with the document but does not hold it. A failed check (E401-E405) is stored in
the company `findings` table (and appended to `audit.findings`); the document is committed but held
(`documents.status = 'held'`, out of `current_facts` and metrics) until the user decides through Claude: save as
is (`fin findings accept`: loaded as filed, findings kept as accepted) or have Claude fix it with the existing tools
(`fin map set` + `fin ingest --force`, `fin review confirm`, `fin sql --write`; held documents are checked again
after each, and a finding no longer found is resolved). Only nothing readable (E1xx/E2xx) fails a document. Claude
works without approval prompts: the guard hook is removed; `fin`'s backup and audit log are the safety net.
`fin status` shows per company the periods loaded and gaps, held documents, findings, pending mappings,
how many figures were read by Claude and by the PDF add-on (information) and inbox files not yet ingested.

Unread statement pages (after 1.0.0, not a phase): a primary statement printed in a PDF but not read is never passed
over in silence. Besides pages skipped for their header or unit (E202/E203), fin reports a titled statement page
with no row of figures, and a page whose title fin does not recognise names a statement
("balance sheet", "statement of assets and liabilities", "profit and loss", "statement of cash flows", "financial
results", ...) above five or more figure lines (E201; the title-like line is searched in the top 12 lines, above the
first figure). Such a page is not taken as the previous statement carried on. Every unread
page is listed under the file's outcome on screen (`not read: <statement> page N: <code> <reason>`) and, when the
file fails, as `unread_pages` in its error.json, so `/ingest` can offer a Claude reading of exactly those pages.
A row with figures under some columns and a blank under others (a new item with no comparative) loads the figures
printed; a blank is no value. Such a row of plain integers loads only above a full row (inside the table body, not
a page number or footer below it). A figure fin cannot place under a column (no label of its own, or text across the
columns) makes the page unread (E204); a line with figures is never taken as a heading.

Chart of accounts (after 1.0.0, not a phase): the seed dictionary covers Schedule III Divisions I, II and III
(NBFCs), the bank Forms A and B, SEBI Reg 33 results and the indirect cash flow. Lines with no current / non-current
split (NBFCs, banks) add straight into `total_assets` / `total_liabilities`; NBFC subtotals such as "Total financial
assets" and memo lines are accounts without a parent. Labels that mean different accounts in different formats
("Loans", "Investments", "Provisions" outside a balance-sheet part) map to the lender accounts when the whole balance
sheet is a lender's (no current / non-current part heading or total, and a lender marker), and are otherwise left to
review.

Analysis core (after 1.0.0, not a phase): standard statements and the full metric set.
- `standard_statements` (view, made per connection by `fin.statements` from the dictionary's account tree; `fin
  sql` has it): for each period and scope the P&L, balance sheet and cash flow in standard order with a depth for
  indentation, every account with a value, and for each subtotal what its mapped lines explain (`explained`,
  `coverage`) plus an "Unmapped / other" line for any difference. `fin statements <co> [--period] [--scope]
  [--json]` prints it; `fin export <co>` writes `reports/<co>/<co>_financials.xlsx` (every period side by side, one
  sheet per statement and scope, and a Metrics sheet).
- `metric_definitions` is one row per metric (category, unit, periods, when_missing, definition, formula);
  `metric_input_accounts` lists each metric's input accounts and periods for `metric_inputs`. Categories:
  profitability, returns (closing and `*_avg` on average balances), liquidity, solvency, efficiency, cash flow,
  growth (year on year, 3- and 5-year CAGR), earnings quality, per share (EPS as printed), lender (NBFCs and banks,
  only when their accounts exist), trailing twelve months (four consecutive quarters). A metric without its inputs is
  NULL (`when_missing = 'null'`) or absent (`'absent'`), never zero.
- EBITDA is computed from profit before exceptional items (PBT when the period prints none, else the printed profit
  before exceptional items plus any share of associates), so a one-off exceptional item does not move EBITDA, its
  margin, debt/EBITDA, interest cover or EBITDA growth.
- `fin statements` and `fin export` show money in the unit the company's documents use (the most recent document's
  when they differ); `--json` stays in rupees with a `unit` per row.

Reports and dashboards by category (after 1.0.0, not a phase):
- Health report: a summary of key metrics over the latest five fiscal years (both scopes where present); one
  section per metric category with data (trend table and an SVG chart drawn by `fin.report.chart`, no library);
  latest quarters (TTM, growth on the year before and the previous quarter) when quarters are loaded; a peer
  comparison with `fin report <co> --peers a,b` (key metrics for the latest fiscal year every company has); findings
  with sources; data coverage (`statement_coverage` view: mapped share per statement;
  `documents.unread_pages`: statement pages fin could not read; unconfirmed mappings; missing data, with its scope:
  the years and quarters shown, the other scope's summary table and the narrative's metric references); and
  appendices (condensed standard statements: every printed subtotal, marked † when the mapped lines under it do not
  add up to it exactly, a subtotal with no mapped line under it as printed; metric definitions; facts behind the
  figures shown, for both scopes and the quarters, the condensed statements' subtotals and the narrative's
  references, each line of a summed value listed, per-share facts unscaled; peers' facts are in their own reports).
  Narrative references accept quarter and half-year periods (`{{metric:ttm_revenue:FY26-Q2}}`).
- Dashboards: `analytics.duckdb` also holds metric categories and definitions, the standard statements (in the
  documents' unit), findings, coverage and unread pages. Company page by category
  (trend charts, any metric, quarters, statements for a chosen period, findings, coverage); compare page (category,
  then metric, across companies); data quality page (findings, verify findings included; held documents, coverage, unread
  pages). An analytics file missing a table is made again at the next refresh (it is derived).

Simpler PDF parser (after 1.0.0, not a phase; owner decision): the product is the analysis, not the parsing. The
parser reads clean, standard digital statements; a page it cannot read goes to the PDF add-on when one is
chosen, and what is still not read is listed and a Claude reading fills it. Each parsing rule was kept only if it reads a statement correctly in a document it was not written for, or
if removing it would load a wrong figure; document-specific workarounds were removed (CHANGELOG "Simplify the PDF
parser"). A page whose figures look misread (a row label holding a figure, a figure with an unpaired bracket) is
not read (E204), and so is an untitled page carrying on a page that was not read. Standalone and consolidated
columns printed side by side are not read (E202).

Diligence (D1) (plan and status: [diligence.md](diligence.md)): a diligence engagement on one company, beside the statement
layer, which is unchanged. New tables in each company database (`db/schema/diligence.sql`, run after
`company_views.sql`; greenfield, no migrations): `engagement` (annual periods in scope, query threshold, open or
closed; at most one open), `accountant_queries` (draft → approved → sent → answered → closed, or withdrawn before
closed; raised from a finding, copying its period, scope, difference and sources without changing it, or by hand)
and `adjustments` (signed effects in rupees for a period of the engagement, a scope and a category from
`config.toml [diligence] adjustment_categories`: a revenue effect, a cost effect and the EBITDA effect `amount`, which
must equal revenue effect minus cost effect; an optional dictionary account, `from_account` and `moved_amount` for a
reclassification between cost accounts; and evidence: a query, a finding or a source reference; proposed → accepted or
rejected). Every row added and every status change is appended to the
audit table `diligence_events`. `config.toml` gets a required `[diligence]` table (`query_threshold`, default
100000.00 rupees; `adjustment_categories`). Commands: `fin engagement open|status|close`, `fin query
list|raise|edit|approve|sent|answer|close|withdraw`, `fin adjust propose|list|accept|reject`; every one but
`engagement open` needs an open engagement (E803); an invalid adjustment is E804; an unknown id or a status change
not allowed is E805. fin never sends a query: `fin query sent` records that the user sent it. Rules in `CLAUDE.md`
("Diligence") and `/diligence`. Views (`db/schema/diligence_views.sql`, run after `diligence.sql`; also in `fin sql`),
on the latest engagement (open or closed) and its annual periods: `adjusted_ebitda` (reported EBITDA, exactly the
`ebitda` metric, plus accepted adjustments; proposed ones summed beside it, not applied; adjusted revenue, the
accepted revenue and cost effects, and margins: the adjusted margin is on adjusted revenue), `ebitda_bridge`
(reported EBITDA, one step per accepted adjustment by id with its revenue and cost effects, adjusted EBITDA) and `ebitda_to_cash` (EBITDA, non-cash and
other, working capital change, taxes paid, other operating, CFO, capex, free cash flow, cash conversion on reported
and adjusted EBITDA; the lines add up to CFO exactly; a missing input makes the lines using it NULL, and a taxes-paid
line not mapped is 0 only when CFO equals cash generated from operations). `fin engagement bridge <co> [--period]
[--scope] [--json]` shows them (money in the documents' unit as `fin statements`; JSON in rupees); E803 when the
company has no engagement. The views have a row per period in scope and scope (the scopes with metrics or
adjustments in those periods, else the company's scopes, else standalone), so a period without data shows with
NULLs. Gates (`fin/diligence/gates.py`, a registry in code, approved in order): G1 Ingestion
(every annual period in scope has mapped figures in `current_facts`, what the metrics use, no held documents, no
mapping items waiting for review; its inputs include the figures, every `current_facts` value by account, period and
scope, so a changed figure reopens it), G2 Reconciliation (no query in draft, and once a bank statement is loaded `bank_vs_invoices` and `bank_vs_tb` have run on current data with every line at or above the threshold in a query; its inputs are each query's id, wording and whether it is a draft and the latest bank reconciliation runs, so a new or
edited query reopens it and sending, answering or closing one does not; unlocks `fin adjust accept|reject`), G4 Adjustments (no
proposed adjustment, every accepted one has evidence; unlocks `fin engagement close`); G3 Recast sits between them (once a sales register is loaded,
for every annual period: the recast ran on the current data, every undated sales invoice at or above the threshold is in a query, the
period's recast difference has a proposed or decided `recast` adjustment; unlocks approving G4; `fin/reconcile/recast_checks.py`).
`fin gate approve <co> <gate>` appends a row to `gate_approvals` (company database) with the sha256 of the gate's
inputs, and a `gate` row to `diligence_events`; it refuses (E802, exit 3) while the checklist fails or an earlier gate
is not approved. A gate is approved while its inputs still hash to the latest approval's, its checklist passes and
every earlier gate is approved; reopened when they changed (`fin gate status` says which input) or its checklist
fails now; open when never approved. `fin engagement close` needs G4, except for an engagement with no queries and
no adjustments, which closes without its gates (nothing to lose; one opened on periods without data could never
close otherwise). A command whose
gate is not approved refuses (E801, exit 3), checked in the same transaction as its change. `fin status` shows an
open engagement's queries by status, proposed adjustments and gate states. Only the user approves a gate: the
PreToolUse hook `gate_guard.py` asks the user before any Bash command that looks like a gate approval (§8). The
diligence tables (`engagement`, `accountant_queries`, `adjustments`, `gate_approvals`) change only through `fin
engagement|query|adjust|gate`: `fin sql --write` refuses any statement other than a SELECT that names one (E806,
exit 2; nothing is run, the refusal is logged in `queries`). Every diligence command that changes data first backs
up the company database to `backups/<run_id>/`.

Diligence source files (D2, first half; docs/diligence.md §7): `fin ingest <co> <file> --kind
trial_balance|ledger_vouchers|registers [--profile <name>]` loads one Tally export or one registers workbook (one file, no `--force` or `--reading`) for
the open engagement (E803 without one). Tables in `db/schema/sources.sql` (run after `diligence_views.sql`):
`source_files` (kind, file name, sha256, archive path, period from the export's header lines, basis `audited`,
profile, loaded or superseded), `trial_balance` (group and ledger rows with group, parent group, closing debit and
credit), `ledger_entries` (one per voucher line, the ledger from its block header), `ledger_blocks` (one per ledger block the export has, even with no voucher lines) and `mask_values`; every row
keeps file_id and sheet + row. Reading is `fin/extract/ledger.py` with a column profile (`fin/extract/profiles.py`,
`templates/profiles/*.toml`: header text to field over `header_rows` rows, date format, period pattern, unit, which
rows to skip, group rows (bold) and nesting (indent), the Ledger Vouchers block field, party groups and masked
fields); the first profile of the kind whose header is found is used, and none is E807 (listing the title and column
header lines, never data rows, known masked names replaced). The period must be one full fiscal year of the
engagement (E808). Meaning is `fin/diligence/tally.py`: a trial balance with no ledger rows is E809 (asks for the
Detailed export); a group whose net closing balance is not the net of the ledgers under it is a finding
(`check_name` `tb_group_sum`, `findings.file_id` set, doc_id NULL), rows kept as filed; Ledger Vouchers need their
period's trial balance loaded first (E811); an undated row with amounts that the profile does not skip is E204 (a
date in another format is never dropped); and the outcome lists the trial balance ledgers missing from them. Masking
(`fin/diligence/mask.py`): ledgers and sub-groups under the profile's party groups (Sundry Debtors: customer, Sundry
Creditors: vendor) are stored as tokens (`CUST-0001`, `VEND-0001`; the same value keeps its first token in every
file, whatever kind it is found as later); known names in other ledger names and in masked fields (Ledger Vouchers
particulars) are replaced as whole words (an employee code is never searched for in free text: a code such as 101 would rewrite amounts); a load that learns new names masks them in the free text already stored (bank remarks and reference, Ledger Vouchers particulars, register text fields, contract billing terms) in the same transaction and says how many earlier values changed, so the result does not depend on load order; the trial balance load lists the groups it treated as party groups (masked) and the other groups, with ledger counts; a Ledger Vouchers ledger not in its trial balance is masked as
`LEDG-0001`. `fin sql` (read or write) refuses SQL naming `mask_values`, or calling `query`, `query_table` or `read_duckdb` (E810), and `fin sql` runs with file access closed (no `read_csv`, `read_blob`, `glob`, `COPY` of a file: it cannot read the original files, the logs or the exports), and `fin sql --write` also refuses schema changes (CREATE, DROP, ALTER, PRAGMA, SET, EXPORT, VACUUM) and refuses to
write `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements` and `bank_lines` (E806). One transaction per file; a later file of the
same kind and period supersedes the earlier one and its findings; the same file again is E105 (even when superseded). A duplicate, layout,
period or refusal error stops the command and loads nothing.

Registers (D3, `--kind registers`; decisions in `docs/decisions/d3-registers.md`): one billing-system workbook with a
sales sheet and a purchase sheet, read by a `registers` profile (`templates/profiles/billing_registers.toml`: a
`[sales.columns]` and a `[purchase.columns]` table, found by header, whatever the sheet names) in
`fin/extract/registers.py`; meaning is `fin/diligence/registers.py`. Each invoice line is a row of `register_lines` (kind
sales or purchase, sheet + row, ids as filed, customer or vendor, handler and submitted-by as `CUST-`, `VEND-`, `USER-`
tokens, known names in the free-text columns replaced, Amount in Rs before GST as filed). The register has no period
header: `source_files.period` names the fiscal years of its first and last invoice date, basis is `unaudited` when any date
is in the stub, lines outside the engagement are loaded and counted, and a later registers file supersedes the earlier
whatever its period. The same invoice number twice (sales: by number; purchases: by vendor and number) is a finding
(`check_name` `register_duplicate_invoice`, the later row against the first, the repeated line's Amount in Rs as the
difference), both rows kept; `fin query raise --finding` then drafts the query. A row that is not an invoice line is E813.

Pass-through (D4, decisions in `docs/decisions/d4-passthrough.md`): `fin engagement passthrough <co> [--customer CUST-0001
... --source "..."] [--clear]` records the user's list of pass-through customers (table `passthrough_customers`: engagement,
customer token, source, run; setting replaces the list, each setting is an `engagement` row in `diligence_events`; E845 for a
token not on a loaded sales register; `fin sql --write` refuses the table, E806); without options it shows the list and the
split. Views in `db/schema/diligence_split_views.sql` (run after `sources.sql`; the registers have no scope, so they are
company-level), on the latest engagement's loaded register files and `register_lines.period` (the engagement period of the invoice
date, NULL outside it): `revenue_split` (per annual period and the stub: sales revenue, core and pass-through revenue, the
pass-through lines with a Purchase Id (software or licence) and without (services), `linked_purchase_cost` (the purchase
register lines of those Purchase Ids, each counted once), `core_revenue_growth` on core revenue only against the previous annual
period in scope, NULL for the stub and the first period) and `passthrough_lines` (each pass-through sales line with the OEM purchase
its Purchase Id points to, with file and sheet!row of both). Claude proposes the `normalisation` adjustment with
`--revenue-effect` minus the pass-through revenue and `--cost-effect` minus the linked purchase cost; the user accepts it. A
reclassification with revenue and cost effects of the same size has no EBITDA effect and changes the margin on adjusted revenue;
one between two cost accounts (`--account`, `--from-account`, `--moved-amount`) has no effect on any total. Golden eval
kinds `core_revenue`, `passthrough_revenue`, `linked_purchase_cost` (period), `adjusted_revenue`, `adjusted_ebitda` (period, scope).

Customer, product, sector, pricing and retention analytics (D9, decisions in `docs/decisions/d9-analytics.md`): views in
`db/schema/diligence_analytics.sql` (run after `diligence_split_views.sql`) on the latest engagement's loaded sales register lines by
`register_lines.period`; money exact DECIMAL(38,2), shares div6 fractions; core and pass-through (`passthrough_customers`) are
separate `segment`s, and retention, cohorts and price/volume/mix are core only and annual only (the stub is never in them).
`customer_revenue` (per period, segment, customer token: revenue, share, rank), `customer_concentration` (top 1, 5, 10 revenue
and share), `customer_retention` (net, gross and logo retention against the previous annual period, expansion, contraction, lost
and new), `customer_cohorts` (cohort = first annual period with positive core revenue; `opening` for the first period),
`sector_mix` (confirmed sector, else `unmapped`), `product_revenue`, `product_pvm` and `product_pvm_summary` (price, volume and
mix of products with a quantity and a rate; continuing, new, discontinued). The `customers` table (token, status
pending/proposed/confirmed/rejected, sector, confidence, reason; `fin sql --write` refuses it, E806) is filled like
`ledger_accounts`: a registers load adds the customers as pending, `fin map customers <co> [file.json]` lists them and stores
Claude's proposals, `fin review list|confirm|reject <co> ... --customers [--sector]` is the user's decision (E868), each step in
`mapping_decisions` (scope `customer`). `fin analytics <co> [--json]` (`fin/diligence/analytics.py`, `fin/cli/analytics.py`; E869)
prints the views. Export: tables `customer_concentration`, `customer_retention`, `sector_mix`, `price_volume_mix` in
`shared/analytics.duckdb` and the dashboard page `dashboards/pages/customers.md`; the health report's `{{section:customers}}`
(`fin/report/customers.py`, data read in `collect`) is a describe-only section, with no narrative, that D7 includes as it is.
Eval kinds: `customer_revenue`, `sector_revenue`, `product_revenue`, `top_customers_revenue`, `retained_revenue`,
`pvm_price_effect`, `pvm_volume_effect`, `pvm_mix_effect`.

QoE outputs (D7, decisions in `docs/decisions/d7-qoe.md`): `db/schema/diligence_nwc.sql` (last of the company schema files).
`nwc_accounts` lists the working capital accounts (receivables, inventories, other current assets incl. current loans and other
financial assets, trade payables, other current liabilities incl. other financial liabilities and short-term provisions; cash,
investments, borrowings, leases and tax are not in it). `nwc_year_end` (and `nwc_year_end_lines`, each ledger with its trial
balance row) is one row per loaded trial balance of the latest engagement, the stub included (basis unaudited), balances as the
statements show them and net = assets - liabilities, with the count of unmapped ledgers. `nwc_monthly_ledgers` says, per working
capital ledger of each Ledger Vouchers period, whether it is `included` in the monthly view: the previous period's trial balance
is loaded (else "no previous trial balance": an opening is never inferred) and the latest `gl_vs_tb` run has its movement equal to
the trial balance (else excluded with the reason); `nwc_monthly_lines` / `nwc_monthly` are the included ledgers' opening plus
cumulative movement at each month end, with the counts included and excluded; `nwc_series` joins year-end and month-end rows for
the dashboard (tables `qoe_ebitda`, `qoe_revenue`, `qoe_walk`, `qoe_nwc` in `shared/analytics.duckdb`, page
`dashboards/pages/diligence.md`). `fin report <co> --diligence [--scope] [--render]` (`fin/report/dd_data.py`, `dd_tables.py`,
`dd_render.py`; `templates/report/diligence.qmd`; E873 no engagement, E874 a bad reference; E602 typed numbers) writes
`reports/<co>/<scope>/diligence_narrative.md` on its first run, then lints it and renders `diligence.html|docx|pdf` and
`diligence_data.json` (every value, the references, the gate states, the data hash). The narrative's numbers are
`{{figure:<name>:<period>}}` references (`dd_data.FIGURES`: adjusted EBITDA and its margins, other income, core and pass-through
revenue, the walk, net working capital and its components, the recast's revenue effect; the period is an annual period or the
stub). The report: basis and gate states, adjusted EBITDA with the bridge, adjustments and evidence (proposed ones marked not
applied), other income, revenue as core and project revenue, the recast, the EBITDA-to-cash walk, working capital (year-end and
monthly, with the ledgers left out), reconciliation summary and queries, collections and ageing (bank_vs_invoices), the customers
section (D9, as in the health report), the stub as unaudited management accounts, findings and unconfirmed mappings, and a sources
appendix (statement cells, register sheets and lines, pass-through lines, trial balance rows). Eval kinds: `nwc_year_end`,
`nwc_monthly` (`fin/evals/qoe.py`).

Service-period recast (D6, decisions in `docs/decisions/d6-recast.md`): `fin recast run <co> [--period FY25]` spreads each sales
register line's Amount in Rs (before GST) over its service start and end dates by month (by service day, each month's share rounded once on the
running total so the months add to the amount) and compares each annual period's recast with the register's amount by invoice date. It is a reconciliation
named `service_recast` in `recon_runs`/`recon_items` (`fin/reconcile/recast.py`; levels `period` (left = recast, right = booked,
difference = the revenue effect), `month`, `schedule` (per invoice whose recast and booked amounts differ) and `invoice` with kind
`undated`: a sales invoice with no usable service dates, recognised on its invoice date, listed; at or above the threshold it is for
`fin query raise --items`). The command proposes one `recast` adjustment per period for the difference (revenue effect = EBITDA
effect, account `revenue`, evidence `service_recast FY25 (recon #n)`); running again replaces a stale proposal of its own (rejected,
"superseded by recast run #n") and never overrides an accepted or user-rejected one (`fin/reconcile/recast_run.py`). The stub is not
recast. `fin recast contracts <co> [<file> --reading <csv>]` loads Claude's reading of a contract document (table `contracts`: customer
token, invoice number, start, end, value, billing terms, file + page, read_by; E864, E865) as evidence named beside an undated line; it
never drives the schedule. `engagement.fiscal_start_month` (config at open) says which months a period holds. E863: the recast cannot run.
Golden eval kinds `recast_revenue`, `recast_difference`, `recast_undated` (period).

FDD contracts and walkthroughs (F1, `docs/fdd.md`, decisions in `docs/decisions/fdd-f1.md`). `fin recast contracts` also loads vendor
contracts and contract terms: the reading adds `party_kind` (customer or vendor), `counterparty` (`customer` is still read as it),
`lock_in_months`, `notice_days`, `escalation_pct`, the free-text `sla_terms`, `penalty_terms`, `escalation_terms`, `renewal_terms` (known
names masked) and the yes/no clauses `auto_renew`, `termination_convenience`, `change_of_control`, `exclusivity`; an empty cell is "not read"
(NULL), not "no clause". A vendor is checked against the purchase register, a customer against the sales register (E865).
View `contract_coverage` (`db/schema/fdd_contracts.sql`): the customers (core revenue, pass-through customers left out) and vendors
(purchases) of the engagement's last annual period, ranked by Amount in Rs, each with its contract that ends last: on file or not,
expired, expires within 12 months of the period end, auto-renew, change of control, lock-in months left. `fin contracts coverage <co>`
shows the top `[fdd] coverage_top_n` (default 10, config.toml; a file without `[fdd]` uses the default) of each; the data function is
`fin.reconcile.contract_coverage.coverage`. `fin sample draw <co> --kind sales|purchase --period P [--n 25] [--seed S]` (`fin/sampling/draw.py`)
takes every register line at or above the engagement threshold (largest first, up to n), then draws the rest at random from the lines below
it; the seed is stored on the sample (`samples`, `sample_lines`), so the draw repeats; `fin sample show <co> [id]` lists samples and lines.
`fin walkthrough record <co> <sample-id> <file.json>` (`fin/sampling/walkthrough.py`) stores Claude's results, one per line and check
(`in_register`, `contract_covers`, `service_in_contract`, `bank_matched`, `ledger_posting`; pass, exception or not_available; evidence required,
file and page optional; table `walkthroughs`, read_by claude, not verified). View `walkthrough_summary` per sample: lines, lines walked, checks
passed, exceptions, not available, exception lines and amount (`fin walkthrough summary <co>`; data function
`fin.sampling.walkthrough.summaries`). Recording runs the reconciliation `walkthrough` (`fin/reconcile/walkthrough.py`, in `RECONCILIATIONS`, so
`fin reconcile` and gate G2 see it): one invoice-level line per sample line with an exception (left = its Amount in Rs, no right side, note =
the failed checks with their evidence); a line at or above the threshold is grouped into a query (`fin query raise --items`), below it is listed.
E901-E903 (and E864-E865 for the contracts). `fin sql --write` refuses `samples`, `sample_lines` and `walkthroughs` (E806). Golden eval kinds
`contract_value` (ledger = contract id) and `sample_exception_amount` (period, ledger = sample id).

Ledger mapping, `tb_vs_statements`, the stub and G1's trial balance checks (D2, second half). `ledger_accounts`
(`fin/diligence/ledgers.py`): a trial balance load adds its new ledgers as pending; `fin map ledgers <co>` lists
them and `fin map ledgers <co> <file.json>` stores Claude's proposals (`{ledger, account, confidence, reason}`; a
balance sheet or profit and loss account in rupees, else E304; a confirmed ledger is changed only by the user);
`fin review list|confirm|reject <co> ... --ledgers` is the user's decision (`--account` to choose another); each step
goes to `mapping_decisions` (scope `ledger`). A confirmed ledger stores its account's statement and side (1 debit,
-1 credit, from the dictionary tree's top lines). `fin reconcile <co> [tb_vs_statements] [--period]`
(`fin/diligence/recon.py`, `fin/cli/reconcile.py`): per annual period with a loaded trial balance, ledger balances
roll up to the nearest of their account and its subtotals that is a detail line of the standalone statement
(`current_facts`; a figure with no figure below it), and the profit and loss ledgers also roll into `other_equity`
(Tally lists the year's income and expenses beside the opening Profit & Loss A/c). One `recon_runs` row per period
(with the hash of what it compared) and `recon_items` per account (`matched`, `difference`, `left_only`,
`right_only`) and per unmapped ledger (`unmapped`). A line at or above the engagement threshold gets a draft query
(origin `recon`; one from an earlier run with the same account and difference is kept), unless a ledger is unmapped;
lines below are listed. A draft from an earlier run whose line is not in the new run (a mapping or figure changed) is
listed as stale; fin never withdraws it. E812: a period not annual, no trial balance or no statement figures. The stub
(`fin engagement open ... --stub FY27-YTD-SEP`, `fin/diligence/stub.py`): the year to date of the fiscal year after
the last period, columns `stub`, `stub_start`, `stub_end` on `engagement` (a bad stub is E808); its exports load
with basis `unaudited` and the view `stub_figures` rolls its trial balance up by confirmed mapping, labelled
"unaudited management accounts", never annualised. G1 (`fin/diligence/tb_checks.py`), once the engagement has a
source file or a stub: a trial balance and Ledger Vouchers per period and the stub, every trial balance ledger in its
Ledger Vouchers (a ledger block, even one with no voucher lines in the year), every ledger mapped, each annual period's latest `tb_vs_statements` on the current data with every
line at or above the threshold in a query, the stub marked unaudited; its inputs add the source files, the ledger
mappings and the latest reconciliations. `fin sql --write` refuses `ledger_accounts`, `recon_runs`, `recon_items`, `query_items`
(E806).

D4 reconciliations (`fin/reconcile/`, `docs/decisions/d4-recon.md`): `fin reconcile <co> [name] [--period]` (no name: every
one whose inputs are loaded) also runs `sales_register_vs_gl`, `purchase_register_vs_gl` (register Amount in Rs vs
the Ledger Vouchers entries of the ledgers confirmed to revenue, or purchases of stock-in-trade and cost of
materials; month, party and invoice lines; a foreign-currency invoice differing only by rate is an `fx` line),
`gl_vs_tb` (Ledger Vouchers movement vs trial balance, per ledger, `not_reconciled` without a prior trial balance) and
`payroll_vs_gl` (annual), for the annual periods and the stub. One `recon_runs` row per run (`right_file_id`, input
hash); `recon_items` gain `item_id`, `level`, `key`, `needs_query`, `note` and the kinds `fx`, `not_reconciled`. fin
drafts no query for these: `fin query raise <co> --items 12,13 --question "..."` makes one draft issue (origin
`recon`) from lines of one period (`query_items`; E841, E842); a rerun keeps a query on a line whose level, key,
kind and amounts are unchanged. G2 requires every reconciliation whose inputs are loaded to have run on the
current data with every `needs_query` line in a query (E840: an input missing or a ledger not mapped). Golden
eval kinds: `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`.

**Query export (D8).** `fin query export <co> [--brief]` writes the open engagement's approved queries to
`exports/<company>/<run_id>/`: `queries.xlsx` (Summary; `Q<id>` per issue with its lines and the count and value of
each kind of difference from `query_items`; `S<n> <sheet>` copies of the cited source sheets from the archive with the
lines' rows highlighted and the query named beside them) and `email.txt` (a draft covering email), or `query-<id>.docx`
(a 1-2 page brief per issue) with `--brief`. The export is the only reader of `mask_values`: tokens in the wording
and lines become real names (a token with no real value is E861); fin prints only the paths, and nothing else it prints,
logs or stores (except `mask_values`) holds a name. The database does not change: the user sends the files, then
`fin query sent`. `fin query answer <co> <id> --file reply [--text ...]` archives the reply like a source
(`answer_source` names it); a .txt/.eml/.md reply is read with known names masked and is the answer unless `--text` is
given, any other file needs `--text` (E860). Errors E858-E861; choices `docs/decisions/d8-query-export.md`. Golden eval
kinds: `query_issue` (the issue's amount) and `query_difference` (one kind of difference, ledger `<id> <kind>`); both are
in rupees, a foreign-currency line at its rupee value (`fin.reconcile.items.line_rupees`), the same figure the query's
amount, `fin query raise --items` and the export use.

Bank matching (D5; `docs/decisions/d5-bank-match.md`). `fin reconcile <co> bank_vs_invoices [--period]`
(`fin/reconcile/bank.py`, lines in `bank_lines.py`, passes in `bank_match.py`; registered with the D4 reconciliations) matches the loaded bank statements' credits
(receipts) with sales register invoices in fixed passes: the invoice number in the receipt's reference or remarks
(`reference`; several receipts or invoices naming each other form one group, the amounts need not agree), same customer +
amount + dates at most `[diligence] match_window_days` apart (`party_amount_date`, the closest dates first), same amount
+ window when neither side has another candidate and the customers do not disagree (`amount_date`); `confirmed` is a
match Claude proposed (`fin reconcile <co> match --propose <file.json>`, table `match_proposals`) and the user confirmed
(`fin review list|confirm|reject <co> --matches`). Amounts are compared in the receipt's currency (an INR receipt with the
invoice's Amount in Rs, a USD receipt with the invoice's own Amount). One `recon_runs` row per period with a bank
statement (receipts in the period of their statement, invoices in the period of their date, a matched group in its
receipts' period; `as_of` is the period end, `due_days` the engagement's) with `recon_items` (`match_pass`,
`currency`, `amount_inr`, `note`) and `recon_match_lines` (the bank lines and invoices behind each item). Lines at or
above the threshold in rupees (`difference`, `left_only`, `right_only`; a foreign line with no rupee value too) are
`needs_query` (grouped with `fin query raise --items`, levels `match`, `receipt`, `invoice`); an `fx` line (the receipt's booked rupees in Ledger Vouchers, found on the customer's ledger within
`fx_window_days` of the bank date at a rate within 10% of the invoice's, else the note `no booked value` naming the vouchers set aside, against the invoice's Amount in Rs) is listed, never queried; an unmatched invoice not
past `invoice_due_days` at the period end is `not_yet_due`, listed. The view `customer_collections` gives, per period and
customer, invoiced, received (matched in full), partly paid (invoices in a group whose receipts differ from them: the invoice amount) and open rupees (invoiced = received + partly paid + open), collection days (matched invoices only) and the overdue ageing of open invoices.
`fin reconcile <co> bank_vs_tb` (`bank.py`) compares the INR statements ending on a period's last day with the trial
balance's cash and bank ledgers. G2 requires both through the D4 checks (`fin/reconcile/checks.py`). The engagement copies `match_window_days`, `fx_window_days`, `invoice_due_days` and `fiscal_start_month` from `config.toml` when it opens. E854-E855. Golden check kinds:
`bank_unmatched`, `invoice_unmatched`, `fx_line`, `customer_received`, `customer_partly_paid`, `bank_vs_tb`.

Payroll (D3, `docs/decisions/d3-payroll.md`): `fin ingest <co> <file> --kind payroll` reads the Sparsh snapshot, one
row per employee, with the profile `templates/profiles/sparsh_payroll.toml` (the period pattern has one group,
`date`: "Employee Master as on 31-Mar-2025"). The snapshot date must fall in an annual period of the engagement
(basis `audited`) or in the stub (`unaudited`), else E808; a later file for the same period supersedes it.
`payroll_lines` (`db/schema/sources.sql`) keeps file_id, sheet, row, snapshot date, employee name and code as tokens
(`EMP-0001`, `ECODE-0001`), designation, department, vertical, sub-vertical, joining and resign date, gross salary
and CTC (annual rupees; a blank cell is NULL; no month column). A row with no name or code, or a bad date, is E821;
an amount that is not a number is E204; rows named Total are skipped (profile `skip`). fin cannot know the unit, so
it warns when the median gross salary or CTC is under Rs 1,50,000 (`MONTHLY_BELOW` in `fin/diligence/payroll.py`),
at load, in `confirm-unit` and in G1; `fin payroll confirm-unit <co> <file> --annual|--monthly` records the user's
answer in `payroll_units` (one row per file, replaced by a later answer, logged as an event; E820 for no loaded
payroll file). G1 gains one line per loaded payroll file (none without one): passes on `annual`, fails when
unconfirmed or `monthly` (fin never converts amounts); its inputs add the confirmations. `fin sql --write` refuses
`payroll_lines` and `payroll_units` (E806).

Bank statements (D3, bank; decisions in `docs/decisions/d3-bank.md`): `fin ingest <co> <file> --kind bank [--profile]`
(`fin/diligence/bank.py`) loads one Excel or PDF statement for the open engagement. Profiles `bank_icici`, `bank_sbi`,
`bank_axis` (kind `bank`: the table's columns, `bank`, default `currency`, and `[header]` regular expressions for the
account, currency, opening and closing balance in the lines around the table) read INR and USD accounts alike; an
Excel file is read by `extract/ledger.read_table`, a PDF by `diligence/bank_pdf.py`: the chosen `[pdf] parser` reads
every page once, its tables and text lines are saved as `cache/bank/<sha256>.json` (a second ingest of the same bytes
never calls the parser; `bank_statements.parse_path` and the rows' page + row trace to it), and the same profile reads
the saved parse (a table with the profile's header starts the transactions, a headerless table of the same width
continues them). Tables `bank_statements` (one per file: bank, account, currency, period, opening and closing
balance with where each came from, totals, `ties`, reader, parse path) and `bank_lines` (sheet or page, row, date,
remarks and reference with known masked names replaced, debit, credit, balance), money in the account's own
currency. The period of `source_files` is the engagement period (or the stub) holding all the statement's dates
(E808 across a year end); bank files are never superseded (the same bytes again are E105). A statement whose opening
+ credits − debits is not its closing balance is a finding (`bank_tie`, rows kept as filed); `diligence/bank_checks.py`
records `bank_overlap` and `bank_balance_gap` findings across the statements of one account after every load. G1 adds
"every bank parse ties" (a `bank_tie` finding accepted by the user counts); overlaps and gaps are listed, not
blocking. E816-E818; LlamaParse's key and failures are `llamaparse_run`'s (E206).

FDD provisions, payroll liabilities and cut-off (F3, `docs/fdd.md`; decisions in `docs/decisions/fdd-f3.md`): the
dictionary has no account for the provision for doubtful debts or the gratuity provision, so Claude proposes the trial
balance ledgers that hold them (`fin provisions propose <co> <file.json>`, a JSON list of `{ledger, kind, reason}`
with kind `doubtful_debts` or `gratuity`; E920) and the user confirms or rejects them (`fin provisions confirm|reject
<co> <ids>`, E921; `fin provisions ledgers <co>` lists them; each change is a run event). Table `provision_ledgers`
(`db/schema/fdd_bsheet.sql`), refused by `fin sql --write` (E806). `fin provisions show <co> [--period P] [--json]`
(E923 without an engagement) prints, for the latest engagement:

- receivables ageing (view `receivable_provision`, lines `receivable_open_lines`): at each year end whose period has a
  `bank_vs_invoices` run, the sales invoices open at that date from the latest run per period (unmatched, or the part of a
  match group's invoices not covered by its receipts dated by the year end, shared over its invoices by amount), in buckets of
  0-90, 91-180, 181-365 and over 365 days from invoice date, each with its share of the total; against the confirmed
  doubtful debts ledgers' credit less debit in that year's trial balance (`provision_balances`), with coverage = the
  provision over the bucket and every older one (over the total for `total`). Sales invoices dated by the year end
  that are in no run (no bank statement for their period) are shown as not aged.
- indicative gratuity (table macros `gratuity_lines` and `gratuity_indicative(basic_share, min_years, cap)`, called
  with `config.toml [fdd]`): per payroll snapshot whose unit the user confirmed annual, each employee not resigned by
  the snapshot date with at least `gratuity_min_years` (5) completed years: 15/26 x monthly wages x years, capped at
  `gratuity_cap` (Rs 20 lakh), with monthly wages = gross salary / 12 x `basic_share` (0.5, an assumption), and a
  second figure under the wage-code definition (wages at least half the CTC); employees with no joining date or gross
  salary are counted as incomplete. Totals against the confirmed gratuity ledgers, with the shortfall. Always labelled
  "indicative, undiscounted, not an actuarial valuation" with the assumption. Leave encashment is not computed.

`[fdd]` in `config.toml` is optional: a file without it, or without one of its keys, uses the plan's values
(`FDD_DEFAULTS` in `fin/config.py`); an unknown key or a value out of range is E002. `cutoff_days` and
`unrecorded_days` are copied onto the engagement when it opens (columns of `engagement`, like the bank matching
settings). Cut-off is two more reconciliations (`fin/reconcile/cutoff.py`), run by `fin reconcile <co>` and required by gate G2 where the registers
reach the window: `sales_cutoff` lists sales invoices within `cutoff_days` (15) of a year end whose service starts
on the other side (billed ahead, or billed late); `purchase_cutoff` lists purchase invoices within `unrecorded_days`
(60) after a year end whose service started on or before it (possible unrecorded liabilities). One line per invoice
number (left_only, Amount in Rs); at or above the threshold it needs a query (`fin query raise --items`). Register
lines in the window with no service start, and registers that end before the window does, are listed as not
reconciled. E922 when the period is not an annual period or no register line is in the window. Golden kinds:
`receivable_ageing`, `doubtful_debt_provision`, `gratuity_provision`, `sales_cutoff`, `purchase_cutoff`. The
FDD report (F8) reads F3 through `fin.diligence.provisions.collect(con, fdd)`.

FDD management inquiries (F2, `docs/fdd.md`; decisions in `docs/decisions/fdd-f2.md`): an inquiry is a query for management on a
topic, with no amount and no source document. `fin query raise <co> --topic <topic> --question "..."` drafts it (`--period` and
`--scope` optional; `--amount`, `--source`, `--finding` and `--items` are refused). The topic is one of `config.toml [fdd]
inquiry_topics` (revenue_recognition, cut_off, mis_kpi, accounting_policies, capitalisation, provisions, related_parties, tax,
contingencies, other; E910 otherwise). It is a row of `accountant_queries` (origin `manual`, amount empty) plus a row of
`inquiries` (`db/schema/fdd_inquiries.sql`, refused by `fin sql --write`, E806), so approve, sent, answer, close and withdraw,
the draft check of gate G2 and `fin query export` (email, workbook, brief: the topic in the title, no amount line) work as for
every query; `fin query list` shows `inquiry: <topic>`. `templates/fdd/inquiries.toml` is the standard checklist per topic;
`fin query checklist <co> [--json]` prints each topic with its inquiries, their status and answers, and for a topic with no
active inquiry (none, or only withdrawn) the standard questions, which Claude adapts and drafts; fin raises nothing itself. The FDD
report (F8) reads this through `fin.diligence.inquiries.collect(con, engagement_id, topics)`.

FDD orderbook and revenue visibility (F7, `docs/fdd.md`, decisions in `docs/decisions/fdd-f7.md`). `fin ingest <co> <file> --kind orderbook`
(profile `templates/profiles/orderbook.toml`, needs an open engagement) loads the company's orderbook, one row per order: customer (a token, the
registers' token for the same customer), order number, order date, value, currency, start, end and billed to date, as at the date in the title
line ("Orderbook as at 31-Mar-2025"), which must be in an annual period or the stub (E808); `orderbook_lines`, with file + sheet!row. A row with
no customer, order number or value, or a bad date, is E960 and nothing loads; a blank billed cell is NULL (counted as not billed, listed). Orders
in a currency other than INR are loaded but have no rupee amount ("not converted"). `fin sql --write` refuses `orderbook_lines` (E806).
`fin visibility <co> [--json]` (`fin/reconcile/visibility.py`, view `revenue_visibility` over `backlog_lines`; E961 when no source is loaded)
shows for the 12 months after the last annual period's end, for each of three sources, the items, what is still to come and the part that falls in
that year (straight-line by day over the remaining term), with that part as a percentage of the last period's core revenue: the unbilled
orderbook (value less billed to date, from the later of the start date and the day after the as-at date to the end date; an order with no end date
is in the backlog but not spread); revenue billed but not yet earned at the period end (core sales register lines invoiced by then whose service
period runs past it, the service-period recast's share); customer contract value remaining (F1 contracts ending after the period end, core
customers; a contract whose value was not read has no amount). The sources overlap and are not added together; a source not loaded says so. The
reconciliation `orderbook_vs_register` (`fin/reconcile/orderbook_vs_register.py`, in `RECONCILIATIONS`, so `fin reconcile` and gate G2 see
it) lists, per period, orders whose customer has no sales register line in the period (left_only, the order value) and core customers billed in
the register with no order on the orderbook (right_only, the billing); the register has no order reference, so they match by customer. A line at
or above the threshold is grouped into a query (`fin query raise --items`, level `order`), below it is listed. Golden eval kinds
`backlog_remaining` and `visibility_covered` (period, ledger = the source: `orderbook`, `billed_not_earned` or `contract`). The FDD
report (F8) reads F7 through `fin.reconcile.visibility.visibility(con, engagement_id)`.

GST and income tax (FDD F6, `docs/fdd.md`; decisions in `docs/decisions/fdd-f6.md`): `fin ingest <co> <file> --kind gst
--profile gstr1|gstr3b` (`fin/fdd/gst.py`, through `fin/diligence/sources.py`) loads a GSTR-1 or GSTR-3B summary for an
engagement year or the stub (period line as for a trial balance, E808): one row per return month into `gst_returns`
(taxable value, IGST, CGST, SGST, cess; GSTR-3B also ITC, RCM liability, tax paid in cash and through credit) with
sheet!row. The file is stored as its return (`source_files.kind` `gstr1` or `gstr3b`), so a later file of the same
return and period supersedes it; `--profile` is required (a GSTR-3B layout has every GSTR-1 column). Profiles
`templates/profiles/gstr1.toml` and `gstr3b.toml` (kind `gst`, `gst_return`, `month_format`). A bad, out-of-period or
repeated month is E950 (nothing loaded); months with no row are listed. Tax ledgers: Claude proposes a class for trial
balance ledgers (`fin map tax <co> <file.json>`: `{ledger, class, confidence, reason}`, class one of `gst_output`,
`gst_input`, `rcm`, `tds`, `income_tax`, `mat_credit`; `fin map tax <co>` lists the classes and the ledgers with
none); the user confirms (`fin review confirm <co> <ids> --tax [--tax-class]`) or rejects them (E951); decisions go to
`mapping_decisions` (scope `tax`). Reconciliations (`fin/reconcile/gst.py`, in `fin reconcile` and gate G2 once their
inputs are loaded): `gstr1_vs_sales_register` (GSTR-1 taxable value vs the sales register's Amount in Rs by invoice
month), `gstr3b_vs_gstr1` (taxable value and output tax), `gstr3b_vs_books` (GSTR-3B output tax vs credits on the
output ledgers; ITC vs debits on the input ledgers; by Ledger Vouchers month) and `rcm` (RCM liability vs credits on
the RCM ledgers): one line per month and measure (level `gst`), a line at or above the threshold for a query
(`fin query raise --items`). Views: `tax_charge` (per annual period and scope from the statements: PBT, current,
deferred and total tax, effective tax rate, net deferred tax asset and its movement, MAT credit from the confirmed
ledgers) and `tax_ledger_balances`. `fin sql --write` refuses `gst_returns` and `tax_ledgers` (E806). Golden eval
kinds `gst_return` (`ledger` `gstr3b 2024-05 itc`) and the four reconciliation names. The FDD report's tax section
reads `fin.fdd.tax_data.collect`.
