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

### Direction (owner, 2026-11-28)

This file describes what fin does today. What is being built next is in [specs/](specs/README.md), written after the
first run on a client's documents ([test-run/2026-11-27-client.md](test-run/2026-11-27-client.md)):

- **The web UI is the main interface.** A user who is not technical works in the browser from adding files to
  downloading a report; chat is for judgement (questions, readings, proposals, narrative). The CLI stays as it is
  and both call the same functions.
- **Who it is for.** A diligence user at an investment firm, working on a company's own documents next to third-party
  databooks, who wants figures in their unit and layout and a trail for every number.
- **Order of work:** guardrails and the client-run fixes; ingest, mapping, statements and diligence in the UI;
  install; and the three pieces the client asked for: data-room intake, statements rebuilt from the books, and one
  list of all differences down to the transaction. Other AI providers through an API key are optional and on hold
  until the owner says so.

Nothing in this section is behaviour until its PR changes the sections below.

## 2. Constraints

- macOS (Apple Silicon and Intel) and Linux (Debian and Ubuntu, x86_64 and aarch64), single user. 16 GB RAM is sufficient.
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
| CI | GitHub Actions | Linux on every PR and main push (including the Linux install in an `ubuntu:24.04` container); macOS on main, on demand and on a PR that changes the install |
| System deps | macOS: Homebrew `Brewfile` and Quarto's release tarball; Linux: `install.sh` (apt-get and pinned downloads) | uv, node, quarto, poppler |

Python dependencies are pinned exactly in `uv.lock`, and run on uv's own Python 3.12 (`.python-version`). Homebrew cannot pin versions, so on macOS `install.sh` takes its current
uv, node and poppler, and quarto as the pinned release tarball named in `install.sh`, unpacked in the home folder (no sudo); on Linux it takes uv and node (the newest 24.x) as they are released, quarto as the pinned
`.deb` version named in `install.sh`, and poppler from apt. `fin doctor` checks minimum versions for uv, node and quarto. CI pins the tested
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
  docs/                  # product.md, diligence.md, development.md, errors.md, client-handoff.md, client-questions.md, eval-kinds.md, decisions/
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
  config.toml             # tolerances, display units, defaults, [dataroom] archive limit
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
  dataroom/<company>/<sha256[:16]>/  # a data-room archive unpacked by `fin dataroom scan` (one folder per archive)
  evidence/  reports/  logs/  backups/
```

## 7. Data model

**Per company (`companies/<company>.duckdb`)**

- `documents` — doc_id, file_name, sha256, version, fiscal_year, period_end (latest in the document), scope (standalone/consolidated), status, run_id, superseded_by, ingested_at
- `raw_values` — raw_id, doc_id, sheet, cell, page, bbox, raw_label, raw_value_text, raw_unit, run_id
- `facts` — fact_id, raw_id, account, fiscal_year, period, period_months, period_end, scope, value `DECIMAL(20,2)` in rupees, mapping_id, confirmed, run_id
- `document_facts` and `current_facts` (views): one value per account, period and scope (lines a document maps to the same account are summed, a total line is not added to its parts; nil lines are left out of this test; of just two equal lines, one is the total only when it is labelled "Total..." or the other sits under it)
- `notes` — doc_id, section, markdown_path
- `fact_sources` (view): each fact with its file, `sheet!cell` or page, printed label, confirmation, reader and document status
- `metrics`, `metric_definitions`, `metric_input_accounts` (views) and `metric_inputs` (metric → fact_ids lineage)
- `saved_metrics`, `saved_metric_operands` (tables) and `saved_metrics_wide` (view rewritten by `fin metric`): saved metrics; the shared ones also in `shared/dictionary.duckdb` `saved_metrics`
- `standard_statements` (temporary view per connection, `fin.statements`): the standard P&L, balance sheet and cash flow with coverage
- `rebuilt_statements` (temporary view per connection, `fin.rebuilt`) over `rebuilt_ledgers` (`rebuilt.sql`): the statements rebuilt from the trial balance (SPEC-14)
- `discrepancies` (temporary view per connection, `fin.discrepancies`; in `fin sql` too): every difference of the latest reconciliations, with its level and parent (SPEC-15)
- Diligence: `engagement`, `accountant_queries`, `adjustments`, `passthrough_customers` (tables) and `engagement_periods`, `adjusted_ebitda`, `adjusted_pl`, `ebitda_bridge`, `ebitda_to_cash`, `revenue_split`, `passthrough_lines`, `customer_revenue`, `customer_concentration`, `customer_retention`, `customer_cohorts`, `sector_mix`, `product_revenue`, `product_pvm`, `product_pvm_summary` (views on the latest engagement; `customers` is a table)
- Diligence source files (`sources.sql`): `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements`, `bank_lines`, `mask_values` (never printed: `fin sql` refuses it, E810), `ledger_accounts`, `payroll_lines`, `payroll_units`, the view `stub_figures`; reconciliations (`reconciliations.sql`): `recon_runs`, `recon_items` (`party`: an invoice line's customer or vendor token), `query_items`, `recon_match_lines`, `match_proposals`

**Shared**

- `dictionary.accounts` — account, statement, parent, sign, description, unit (`rupees`, or `rupees_per_share` for EPS: stored as printed, never in a subtotal) (seeded from Schedule III)
- `dictionary.label_map` — normalized label, statement, section (heading the label sits under, or any), account, scope (global/company), company, mapping_id, source
- `audit.*` — see §8
- `analytics.metrics` — export of every company's metrics for dashboards

**Rules**

- All values stored in rupees as exact DECIMAL. Original unit kept in `raw_values`. How money is shown is set in
  config (`[display]`, "Display units and the databook" below) and converted in SQL.
  A unit line names its unit after "in", "₹", "Rs" or "INR" ("₹ in Lakhs", "INR crores"); "in INR" with no unit
  word after it ("All amounts in INR, unless otherwise stated") means rupees (`fin.extract.units`). A document that
  failed with E203 on such a line loads when it is ingested again. A document with no unit line on a statement
  page or sheet is held (counted as held, nothing loaded, the file in `review/` with `"question": "which unit?"`):
  fin never guesses a unit. With `--reading`, such a PDF page is left for the reading to fill instead. The user answers and `fin ingest <co> <file> --unit lakhs|crores|millions|thousands|rupees`
  loads it; the given unit is used only where no unit line was found (a sheet or page with its own line keeps it),
  each such raw value records `unit given by user` (`, entered by Claude` when Claude Code ran the command), shown by
  `fin audit` and on evidence pages, and `documents.unit_given` / `unit_given_by` keep the answer and the actor.
  `--unit` on a document that needed no unit is refused (E207); it takes one file and not `--kind`.
- Excel workbooks: .xlsx, .xlsm and .xls (the old binary format, read with xlrd into the same reader:
  `fin.extract.xls`). A sheet with a row of figures that has no label (E204) is held on its own: the other sheets
  load, the outcome lists it (`not read: <statement> sheet 'X': E204 ... cells C4, D4`) and it is in
  `unread_pages` with its sheet name, so a Claude reading can fill it. A workbook whose only statement sheets are
  such sheets fails with E204.
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
- An error event carries what was seen in its `context` (`FinError.with_context`). For a document that fails
  (E1xx/E2xx) the context, also written to `review/<company>/<file>.error.json`, has the file type and size, the
  reader and its version (pdfplumber or openpyxl), the PDF add-on chosen (`[pdf] parser`), the reading given, the
  statement pages left unread, and under `seen` what the reader found: page and image counts of a scanned PDF, the
  top lines of the page or sheet where a unit line, period header or statement title was looked for, the cells of a
  row with no label (`fin.extract.diagnostics`). The lines are document text; like the labels in the log, they stay
  in the data folder. The notice for pages no add-on read names the pages and the parser in its context.
- An exception with no error class is logged as E012 with its traceback and the platform. The user sees no
  traceback: the command prints `E012: unexpected <type>: <message>`, the run_id and the log file that holds the
  traceback, and exits 2; in the web UI the action shows the same line, code and run_id. An interrupt is logged with
  no code and stops the command as before.

**Tables in `shared/audit.duckdb`** (append-only; rows never updated or deleted)

| Table | Records |
|---|---|
| `runs` | run_id, command, args, start, end, status, exit code, git commit, fin/DuckDB/Python versions |
| `events` | run_id, stage, file, level, error code, message, context JSON |
| `mapping_decisions` | raw label → account, actor (`user` or `ai:<provider>`, see "Actor" below), on_request, confidence, reason, timestamp |
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
- A figure whose raw value records no sheet, cell or page has no source: `fin sql` gives it `source_ref` NULL
  ("no source" inside a sum). `--evidence` still writes the page with the sources of the other rows, names each
  such figure, flags the result rows with no source (no id, or a figure with no source behind them) and lists their
  numbers, from 1, in the output's `rows_without_source`. It is not a failure: the run exits 0.

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
git clone <repo-url> ~/fin-analyst && cd ~/fin-analyst
./install.sh             # or ./install.sh --parser docling|liteparse|llamaparse for the optional PDF add-on
claude
```

Clone into a folder the user chooses (default `~/fin-analyst`), never a temporary one: `install.sh` warns when the
repo is inside `/tmp`, `/var/tmp`, `/var/folders` (with their `/private` forms on macOS) or `$TMPDIR`, and so
does `fin doctor` (`repo folder`).

`install.sh` (idempotent, logs to `logs/install.log`; `--no-ui` leaves out the web UI):

1. Check the OS and architecture: macOS (arm64, x86_64) or Debian/Ubuntu Linux (x86_64, aarch64). Any other Linux
   stops here with the list of tools to install by hand.
2. macOS: install Homebrew if missing (asks first). Linux: nothing (root, or sudo, is needed for step 3).
3. macOS: `brew bundle --file=Brewfile` (uv, node, poppler); then Quarto, unless one at `doctor`'s minimum is
   found (`~/.local/bin` first, then PATH): the pinned release tarball, checked against its published SHA-256,
   unpacked into `~/.local/opt/quarto-<version>` and linked from `~/.local/bin/quarto`, with no sudo (the Homebrew
   cask runs an installer that needs a password, which an agent has no terminal to type in). fin looks for tools in
   `~/.local/bin` before PATH (`fin.systools`). Linux: only what is missing or too old, as `doctor`'s
   minimums define it: poppler-utils, curl and xz-utils with `apt-get`; uv from its
   GitHub release; node (newest 24.x, from nodejs.org, linked into `/usr/local/bin`); quarto from its pinned GitHub
   release `.deb`. Downloads are checked against the published SHA-256. A kept tool is never replaced, and a second run
   prints `all system dependencies already installed`. As root it runs `apt-get` directly; otherwise it uses `sudo`
   (`sudo -n` when there is no terminal).
   A failed `brew bundle` (a minute apart) or `apt-get` (30 s apart) is tried again, up to 3 times, only when its
   output shows a network failure (a name lookup, a refused, reset or timed-out connection, an HTTP 5xx). Any other
   failure, such as a password prompt with no terminal or a missing permission, stops at once with the failing
   command and what to do.
3a. uv's own Python: `UV_PYTHON_PREFERENCE=only-managed` for every uv command of the install, `uv python install`
   of the version in `.python-version`, so a `python3` on PATH is never used. A `.venv` made from another Python
   (its `pyvenv.cfg` `home` outside `uv python dir`) is removed and made again.
4. `uv sync --frozen --extra ui` (the web UI, left out with `--no-ui`, which removes it when installed) (with `--extra <name>` for the PDF add-on `--parser <name>` names, or `config.toml`'s
   `[pdf] parser` without the flag; `--parser none` removes it).
5. `npm ci --prefix dashboards`
6. `uv run fin init` — creates data folder, config, databases from the schema files; then, with `--parser` or an
   add-on in `config.toml`, `uv run fin parser use <name>` downloads its models and records it.
7. `uv run fin doctor` — checks every dependency, version, folder permission, that the databases exist and are not from an older fin.
8. With no PDF add-on chosen, it names the three add-ons with their cost and the command for each; nothing is
   installed without a choice. The last line is the command that opens the web UI (`cd <repo> && uv run fin ui`,
   which opens http://127.0.0.1:8765 with a link token made at start), or, with `--no-ui`, how to install it.

`fin doctor` also checks the install's choices, as warnings with the fix: `python source` (fin runs on uv's own
Python: `sys.base_prefix` inside `uv python dir`; fix: run `./install.sh`), `repo folder` (not in a temporary
folder), the `quarto` fix on macOS (`./install.sh`, never brew), and `web ui` (installed or not).

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
| `fin ui [--port N] [--no-open]`, `fin ui setup` | — | Human (optional web UI; see "Web UI" below) |
| `fin ingest <co> [path] [--force]` | `/ingest` | Either |
| `fin ingest <co> <file> --reading <csv>` | `/ingest` (offered after a failure or skipped page) | Claude, when the user asks |
| `fin ingest <co> <file> --kind trial_balance\|ledger_vouchers\|registers\|payroll\|bank [--profile]` | `/diligence` | Either |
| `fin payroll confirm-unit <co> <file> --annual\|--monthly` | `/diligence` | Either (only when the user says which) |
| `fin ingest-batch [folder]` (default `inbox/`) | `/ingest-batch` | Either |
| `fin dataroom scan <co> <folder> [--json]`, `fin dataroom plan <co> [--json]`, `fin dataroom load <co>` | — | Either (`load` loads only rows the user approved) |
| `fin dataroom propose <co> <id> --kind K [--profile P] --reason R` | — | Claude proposes a kind for a file fin could not name |
| `fin dataroom approve <co> <ids> [--undo]` | — | Either (only when the user says which rows; approving a proposed kind confirms it) |
| `fin reference add <co> <file> --source S [--unit U] [--reading R]`, `fin reference lines\|compare <co> [--all] [--json]` | — | Either |
| `fin reference propose <co> <file.json>`, `fin reference query <co> <#id> --question Q`, `fin reference adjust <co> <#id> ...` | — | Claude proposes or drafts; the user approves |
| `fin reference confirm\|reject <co> <ids>` | — | Either (only when the user says) |
| `fin verify <co> <file> --reading <csv>` | `/verify` | Either (when the user asks) |
| `fin map propose <co> <file.json>` | used inside `/ingest` | Claude |
| `fin map set <co> --statement --label --account [--section]` (applied to the loaded facts, their documents checked again: no re-ingest), `fin map accounts` | `/ingest` (fixing a finding) | Either |
| `fin review list [co]`, `fin review confirm\|reject <co> <ids> [--each]` (a proposal below confidence 0.8 is confirmed only named alone or with `--each`; a bulk confirm skips it and says so, and refuses with E304 when every id is low) | `/review` | Either (confirm when the user says) |
| `fin findings list [co]`, `fin findings accept <co> <ids>` | `/ingest`, `/verify` | Either (accept when the user says save as is) |
| `fin document delete <co> <ids>` (the Files screen's Delete) | — | Either (when the user asks) |
| `fin status [co]` | `/status` | Either |
| `fin sql <co> --sql "…" [--question] [--evidence]` | `/ask`, `/report` | Either (reads) |
| `fin sql <co> --write --sql "…"` | — | Either (on user request; backup + audit; refuses the figure tables, E505) |
| `fin fact state <co> --account --period --scope --value --reason`, `fin fact unstate <co> <id>`, `fin fact correct <co> <fact_id> --value --reason`, `fin fact list <co> [--json]` | `/ingest` | Either (state or correct only when the user says, in their words) |
| `fin schema [co] [--table name] [--json]` | `/ask` | Either |
| `fin metric add <name> --formula … --unit ratio\|rupees\|days --definition "…" [--company co]`, `fin metric list [co]`, `fin metric remove <name> [--company co]` | `/ask` (offers to save a hand-written ratio) | Either (add or remove when the user says) |
| `fin statements <co> [--period] [--scope] [--json] [--rebuilt] [--layout blocks\|databook] [--unit u] [--of-revenue] [--yoy]`, `fin export <co>` | `/statements` | Either |
| `fin report <co> [--scope] [--peers] [--render]` | `/report` | Either |
| `fin dashboard [--build]` | `/dashboard` | Either |
| `fin audit <co> [metric period \| --fact id]` | `/audit` | Either |
| `fin delete <co> [--yes]` | — | Either (typed confirmation, or `--yes` when the user asked) |
| `fin update` | — | Human |
| `fin engagement open <co> --periods FY24,FY25,FY26 [--threshold] [--due-days N]`, `fin engagement status\|close <co>` | `/diligence` | Either (`close` needs gate G4 approved) |
| `fin engagement terms <co> --due-days N` | `/diligence` | The user's credit terms; Claude changes them only when asked |
| `fin engagement bridge <co> [--period] [--scope] [--json]` (views `adjusted_ebitda`, `ebitda_bridge`, `ebitda_to_cash`) | `/diligence` | Either |
| `fin query list\|raise\|edit <co>` | `/diligence` | Claude drafts |
| `fin query approve\|sent\|answer\|close\|withdraw <co> <ids>` | `/diligence` | Either (when the user says; `sent` only once the user sent it) |
| `fin engagement passthrough <co> [--customer ... --source] [--clear]` | `/diligence` | The user's list; Claude records it when given |
| `fin engagement rate <co> [<CUR> <rate> --as-at <date>]` | `/diligence` | The user's exchange rate for orders not in INR; Claude records it only when given |
| `fin map customers <co> [file]`, `fin review list\|confirm\|reject <co> --customers` | `/diligence` | Claude proposes sectors; the user decides |
| `fin analytics <co> [--json]` | `/diligence` | Either |
| `fin recast run <co> [--period]`, `fin recast contracts <co> [<file> --reading <csv>]` | `/diligence` | Claude runs it; the user accepts or rejects the proposed adjustments |
| `fin contracts coverage <co> [--json]`, `fin sample draw <co> --kind sales\|purchase --period P [--n] [--seed]`, `fin sample show <co> [id]`, `fin walkthrough record <co> <sample-id> <file.json>`, `fin walkthrough summary <co>` | `/diligence` | Claude runs them; a walkthrough exception at or above the threshold is grouped into a query the user approves |
| `fin query raise <co> --topic T --question "..."`, `fin query checklist <co>` (FDD F2) | `/diligence` | Claude drafts inquiries; the user approves |
| `fin provisions show\|ledgers\|propose <co>` (FDD F3) | `/diligence` | Claude proposes provision ledgers |
| `fin provisions confirm\|reject <co> <ids>` | `/diligence` | Either (when the user says) |
| `fin netdebt show <co> [--period] [--json]`, `fin netdebt propose <co> [file]`, `fin netdebt item propose\|list <co>` (FDD F4) | `/diligence` | Claude proposes classes and items |
| `fin review confirm\|reject <co> <ids> --netdebt`, `fin netdebt item accept\|reject <co> <ids>` | `/diligence` | Either (when the user says) |
| `fin adjust propose\|list <co>` | `/diligence` | Claude proposes |
| `fin adjust import <co> <file.csv\|xlsx\|xlsm> [--sheet]` (or the walk-sheet upload on the Queries screen) | `/diligence` | Either |
| `fin adjust accept\|reject <co> <ids>` | `/diligence` | Either (when the user says; needs gate G2 approved) |
| `fin gate status <co> [--json]` | `/diligence` | Either |
| `fin gate approve <co> <gate>` | `/diligence` | Human (Claude only when the user says; the PreToolUse hook asks) |
| `fin eval init\|run <co>`, `fin eval bundle [co] [--with-figures]` | `/eval` | Either (only a person marks a golden line checked) |

Metrics are views in each company database; `shared/analytics.duckdb` is rebuilt after every ingest, data change,
verify and update.

## 12. Testing and CI

- **Synthetic fixtures** (`tests/fixtures/sample-financials/`): three fake companies (Kestrel Forgings: Excel with formulas; Bluepeak Polymers: Excel per year, crores, older wording, standalone + consolidated; Tarang Industrial Services: text PDF with notes), known values in `answer_key.json`, plus deliberately broken files. The set is committed, so CI needs no LibreOffice.
- **Golden evaluation** (`fin.evals`, runbook `docs/client-handoff.md`): per company, `evals/<company>/golden.csv` in the data folder (never committed) holds values a person checked in the real documents, with their source. `fin eval init <co>` lists what to check (statement figures in current figures, trial balance ledgers, `tb_vs_statements` lines, stub figures, bank closing balances, register totals per sheet and payroll totals) with source and unit and leaves the value and `checked` empty; only lines marked `checked` are scored. `fin eval run <co>` compares each exactly in rupees after unit conversion (pass, fail, missing in fin, unchecked; the relative difference to six significant digits, so a misread digit in a large figure never shows as 0) and writes a report to `evals/<co>/`, with counts: error codes in the company's runs, findings by code, unread pages, Claude-read figures by document status and latest verify outcome, unmapped labels, mapping items waiting, ledgers by status, bank statements by reader and tie, register lines by kind and payroll files by confirmed unit. `fin eval bundle [co] [--with-figures]` writes a shareable Markdown and JSON file (fin version and commit, doctor summary, scores, lines not passing with their source kind and related codes, counts): companies as `company-N`, ledgers as `ledger-N` (or a token), no file names, labels or sources, no absolute figures without `--with-figures`; E833 refuses one that would contain a masked name, a ledger name, a related party name, a company or a file name (the bundle goes to the fin developers, whom the client's zero-data-retention terms do not cover). Errors E830-E833. Decisions: `docs/decisions/eval-kit.md`.
- **CI** (`.github/workflows/ci.yml`, Linux, every PR and main push): ruff, the file-size check (`tools/check_file_size.mjs`: source files under 6,000 tokens), pytest, `shellcheck install.sh`; one job ingests the synthetic companies once, renders a report (Quarto) and builds the Evidence dashboard (node 24). A `pdf-addon` job per engine syncs its extra: Docling and LiteParse restore their model caches and
run the tests marked `docling` / `liteparse` (the real engine on synthetic PDFs); LlamaParse checks its fixture's
shape against the SDK and never calls the API (its live test skips without `LLAMA_CLOUD_API_KEY`); the other jobs run
without the extras. An `install-linux` job runs `install.sh` twice as root in an `ubuntu:24.04` container with a broken `python3` first on PATH (a second run changes nothing), then `fin doctor`, a smoke ingest and `--no-ui`. `macos.yml` (main, on demand, and a PR that changes `install.sh`, `Brewfile` or `macos.yml`): `install.sh` twice with a `sudo` and a `python3` on PATH that fail when called, `fin doctor` and a smoke ingest; and, for
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
a re-ingest or a mapping is checked as corrected) with the reading's figure converted to rupees with the reading's own unit
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
them and the two ways to read them (`fin parser use <name>`, or a Claude reading). With no add-on chosen, the
outcome on screen offers the add-ons that can read those pages (Docling and LlamaParse only for a scanned PDF), each
with what it costs and whether pages leave the machine (`fin.extract.fallback.ABOUT`); the Files screen of the web UI
shows the same offer while a loaded document or a file in `review/` has skipped PDF pages, and **Install** runs
`fin parser use <name>` as a background job (the cloud one, LlamaParse, asks first). Then the file is ingested again. With a reading, the order is
fin -> the add-on -> the reading fills the statements and scopes neither read.

Findings instead of failures (after 1.0.0, not a phase; owner decision 2026-11-07): fin is an analysis tool, not an
opinionated one. Conflicting data in what a company filed (balance sheet not tying, subtotals not adding up, cash
not reconciling, a figure differing between documents such as a restatement or a quarter vs its year, fin vs a
Claude reading) is a finding to show, never a failure that rejects data. A fin-vs-reading finding (`fin verify`,
above) is kept with the document but does not hold it. A failed check (E401-E405) is stored in
the company `findings` table (and appended to `audit.findings`); the document is committed but held
(`documents.status = 'held'`, out of `current_facts` and metrics) until the user decides through Claude: save as
is (`fin findings accept`: loaded as filed, findings kept as accepted) or have Claude fix it with the existing tools
(`fin map set`, `fin review confirm`; held documents and the loaded documents whose facts changed are checked
again after each, with no re-ingest, and a finding no longer found is resolved). A finding whose statement has
lines mapped in review or with `fin map set` (not by the seed dictionary) names those mappings (`findings.mappings`;
`fin findings list` prints each with `fin map set`), so a wrong mapping can be changed instead of the finding saved
as is. Only nothing readable (E1xx/E2xx) fails a document. Claude
works without approval prompts: the guard hook is removed; `fin`'s backup and audit log are the safety net.
`fin status` shows per company the periods loaded and gaps, held documents, findings, pending mappings,
how many figures were read by Claude and by the PDF add-on (information), how many figures the user stated
(`fin fact state`, information), how many fin misread and were corrected (`fin fact correct`, information),
inbox files not yet ingested, and the checks that did not run on loaded and held documents with what would make
each run ("confirm a cash and a closing_cash figure for FY24 standalone: map its line (Mappings), or state it with
`fin fact state`"; company table `checks_not_run`, replaced each time the document is checked).

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
  [--json]` prints it; `fin export <co>` writes `reports/<co>/<co>_financials.xlsx` (the databook layout: every
  period side by side, one sheet per statement and scope, and a Metrics sheet).
- `metric_definitions` is one row per metric (category, unit, periods, when_missing, definition, formula);
  `metric_input_accounts` lists each metric's input accounts and periods for `metric_inputs`. Categories:
  profitability, returns (closing and `*_avg` on average balances), liquidity, solvency, efficiency, cash flow,
  growth (year on year, 3- and 5-year CAGR), earnings quality, per share (EPS as printed), lender (NBFCs and banks,
  only when their accounts exist), trailing twelve months (four consecutive quarters). A metric without its inputs is
  NULL (`when_missing = 'null'`) or absent (`'absent'`), never zero.
- EBITDA is computed from profit before exceptional items (PBT when the period prints none, else the printed profit
  before exceptional items plus any share of associates), so a one-off exceptional item does not move EBITDA, its
  margin, debt/EBITDA, interest cover or EBITDA growth.
- `fin statements` and `fin export` show money in the display unit (SPEC-06, below); `--json` stays in rupees with
  a `unit` per row (`rupees` or `rupees_per_share`) and adds the converted `shown`.

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
  company's display unit), findings, coverage and unread pages. Company page by category
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
list|raise|edit|approve|sent|answer|close|withdraw`, `fin adjust propose|import|list|accept|reject`; every one but
`engagement open` needs an open engagement (E803), except `fin query list` and `fin adjust list`, which print
"<co> has no open engagement: no queries" (or adjustments; `[]` with `--json`) and exit 0; an invalid adjustment is E804; an unknown id or a status change
not allowed is E805. fin never sends a query: `fin query sent` records that the user sent it. Rules in `CLAUDE.md`
("Diligence") and `/diligence`. Views (`db/schema/diligence_views.sql`, run after `diligence.sql`; also in `fin sql`),
on the latest engagement (open or closed) and its annual periods: `adjusted_ebitda` (reported EBITDA, exactly the
`ebitda` metric, plus accepted adjustments; proposed ones summed beside it, not applied; adjusted revenue, the
accepted revenue and cost effects, and margins: the adjusted margin is on adjusted revenue), `ebitda_bridge`
(reported EBITDA, one step per accepted adjustment by id with its revenue and cost effects, adjusted EBITDA) and `ebitda_to_cash` (EBITDA, non-cash and
other, working capital change, taxes paid, other operating, CFO, capex, free cash flow, cash conversion on reported
and adjusted EBITDA; the lines add up to CFO exactly; a missing input makes the lines using it NULL, and a taxes-paid
line not mapped is 0 only when CFO equals cash generated from operations). `fin engagement bridge <co> [--period]
[--scope] [--json]` shows them (money in the display unit as `fin statements`; JSON in rupees); E803 when the
company has no engagement. The views have a row per period in scope and scope (the scopes with metrics or
adjustments in those periods, else the company's scopes, else standalone), so a period without data shows with
NULLs. Gates (`fin/diligence/gates.py`, a registry in code, approved in order): G1 Ingestion
(every annual period in scope has mapped figures in `current_facts`, what the metrics use, no held documents, no
mapping items waiting for review; the figures the user stated are listed for the user to see before approving and
do not fail it, and a stated figure alone does not cover a period; its inputs include the figures, every
`current_facts` value by account, period and scope, and the stated figures, so a changed or stated figure reopens it), G2 Reconciliation (no query in draft, and once a bank statement is loaded `bank_vs_invoices` and `bank_vs_tb` have run on current data with every line at or above the threshold in a query; its inputs are each query's id, wording and whether it is a draft and the latest bank reconciliation runs, so a new or
edited query reopens it and sending, answering or closing one does not; unlocks `fin adjust accept|reject`), G4 Adjustments (no
proposed adjustment, every accepted one has evidence, no FDD proposal waiting for the user: proposed net debt items,
draft FDD findings, proposed net debt classes, tax classes and related party links (`fin/fdd/gate_checks.py`); its
inputs include those tables, so a change to them reopens it; unlocks `fin engagement close`); G3 Recast sits between them (once a sales register is loaded,
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
rows to skip, group rows (bold) and nesting (indent), the Ledger Vouchers block field); the first profile of the kind whose header is found is used, and none is E807 (listing the title and column
header lines, never data rows). The period must be one full fiscal year of the
engagement (E808). Meaning is `fin/diligence/tally.py`: a trial balance with no ledger rows is E809 (asks for the
Detailed export); a group whose net closing balance is not the net of the ledgers under it is a finding
(`check_name` `tb_group_sum`, `findings.file_id` set, doc_id NULL), rows kept as filed; Ledger Vouchers need their
period's trial balance loaded first (E811: its ledgers are checked against it); an undated row with amounts that the profile does not skip is E204 (a
date in another format is never dropped); and the outcome lists the trial balance ledgers missing from them and the Ledger
Vouchers ledgers not in the trial balance. Masking (`fin/diligence/mask.py`, `docs/decisions/masking.md`): only the
client's fields are tokens: the registers' customer, vendor, handler and submitted-by and the orderbook's customer
(`CUST-0001`, `VEND-0001`, `USER-0001`; the same value keeps its first token in every file, whatever kind it is
found as later) and payroll's employee name and code (`EMP-0001`, `ECODE-0001`), and a bank statement's account
number (`ACCT-0001`, below). The trial balance and Ledger
Vouchers (ledgers, party ledgers too, and particulars), bank remarks and reference, the other register columns and
all free text (contract terms, FDD text, query answers, the reasons stored with proposals) are stored as filed or
written. Party matching: a ledger, a Ledger Vouchers particulars or a bank line that names a register customer or
vendor is matched to its token through `mask_values` (case and spaces ignored; in bank text, as whole words):
`mask.Parties`, used by the register vs ledger and bank reconciliations, and the view `related_party_matches`.
`fin sql` (read or write) refuses SQL naming `mask_values`, or calling `query`, `query_table` or `read_duckdb` (E810), and `fin sql` runs with file access closed (no `read_csv`, `read_blob`, `glob`, `COPY` of a file: it cannot read the original files, the logs or the exports), and `fin sql --write` also refuses schema changes (CREATE, DROP, ALTER, PRAGMA, SET, EXPORT, VACUUM) and refuses to
write `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements` and `bank_lines` (E806). One transaction per file; a later file of the
same kind and period supersedes the earlier one and its findings; the same file again is E105 (even when superseded). A duplicate, layout,
period or refusal error stops the command and loads nothing.

Registers (D3, `--kind registers`; decisions in `docs/decisions/d3-registers.md`): one billing-system workbook with a
sales sheet and a purchase sheet, read by a `registers` profile (`templates/profiles/billing_registers.toml`: a
`[sales.columns]` and a `[purchase.columns]` table, found by header, whatever the sheet names) in
`fin/extract/registers.py`; meaning is `fin/diligence/registers.py`. Each invoice line is a row of `register_lines` (kind
sales or purchase, sheet + row, ids as filed, customer or vendor, handler and submitted-by as `CUST-`, `VEND-`, `USER-`
tokens (the client's starred fields), the other columns and Amount in Rs before GST as filed). The register has no period
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
difference = the revenue effect; with no register line dated in the period booked is missing, not 0), each period and schedule
side naming its register rows (file sheet!row), `month`, `schedule` (per invoice whose recast and booked amounts differ) and `invoice` with kind
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
`lock_in_months`, `notice_days`, `escalation_pct`, the free-text `sla_terms`, `penalty_terms`, `escalation_terms`, `renewal_terms` (as
read) and the yes/no clauses `auto_renew`, `termination_convenience`, `change_of_control`, `exclusivity`; an empty cell is "not read"
(NULL), not "no clause". A vendor is checked against the purchase register, a customer against the sales register (E865).
View `contract_coverage` (`db/schema/fdd_contracts.sql`): the customers (core revenue, pass-through customers left out) and vendors
(purchases) of the engagement's last annual period, ranked by Amount in Rs, each with its contract that ends last: on file or not,
expired, expires within 12 months of the period end, auto-renew, change of control, lock-in months left (whole months from the period end, so a lock-in ending the next day has 0). `fin contracts coverage <co>`
shows the top `[fdd] coverage_top_n` (default 10, config.toml; a file without `[fdd]` uses the default) of each; the data function is
`fin.reconcile.contract_coverage.coverage`. `fin sample draw <co> --kind sales|purchase --period P [--n 25] [--seed S]` (`fin/sampling/draw.py`)
takes every register line at or above the engagement threshold (largest first, up to n), then draws the rest at random from the lines below
it; the seed is stored on the sample (`samples`, `sample_lines`), so the draw repeats; `fin sample show <co> [id]` lists samples and lines.
`fin walkthrough record <co> <sample-id> <file.json>` (`fin/sampling/walkthrough.py`) stores Claude's results, one per line and check
(`in_register`, `contract_covers`, `service_in_contract`, `bank_matched`, `ledger_posting`; pass, exception or not_available; evidence required,
file and page optional; table `walkthroughs`, read_by `ai:claude`, not verified). View `walkthrough_summary` per sample: lines, lines walked, checks
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
(a 1-2 page brief per issue) with `--brief`. The export is the only reader of the real values in `mask_values`:
tokens in the wording and lines become real names (a token with no real value is E861); fin prints only the paths. The database does not change: the user sends the files, then
`fin query sent`. `fin query answer <co> <id> --file reply [--text ...]` archives the reply like a source
(`answer_source` names it); a .txt/.eml/.md reply is read as written and is the answer unless `--text` is
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
balance's cash and bank ledgers. G2 requires both through the D4 checks (`fin/reconcile/checks.py`). The engagement copies `match_window_days`, `fx_window_days`, `invoice_due_days` and `fiscal_start_month` from `config.toml` when it opens.
Credit terms belong to the engagement (the client, 2026-11-27): `invoice_due_days` defaults to 90; `fin engagement open --due-days N` sets
another value for one engagement and `fin engagement terms <co> --due-days N` changes the open one's (E846 outside 0 to 3650 days; the
change is in `diligence_events`). `fin engagement status` and the Gates screen show them. Overdue and the ageing read the engagement's
value, stored on each bank_vs_invoices run (`recon_runs.due_days`) and part of its input hash, so a change makes the run stale and
gate G2 reopens until it runs again. Data folders whose `config.toml` was written before keep the value written there. E854-E855. Golden check kinds:
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
every page once, its tables and text lines are saved as `cache/bank/<company>/<sha256>.json` (with each account number as its
token, also a number a later statement makes known; a saved parse holding a token the company does not know is E818; `fin delete` removes the folder; a second ingest of the same bytes, or one after a failed load, never calls the parser; `bank_statements.parse_path` and the rows' page + row trace to it), and the same profile reads
the saved parse (a table with the profile's header starts the transactions, a headerless table of the same width
continues them). Tables `bank_statements` (one per file: bank, account, currency, period, opening and closing
balance with where each came from, totals, `ties`, reader, parse path) and `bank_lines` (sheet or page, row, date,
remarks and reference as filed, debit, credit, balance), money in the account's own
currency. The account number is masked (the client, 2026-11-27; `diligence/bank_mask.py`): it is stored once in
`mask_values` (kind `bank_account`) and `bank_statements`, `bank_lines`, findings, `fin` output, logs, saved parses,
exports and reports carry its token `ACCT-0001`; the same number gets the same token in every file, so the statements of
one account still chain. A known account number in remarks or reference (a transfer between the company's own
accounts) is its token too, also in the lines loaded before that account was. A statement no profile reads (E807)
lists its title lines with every run of nine or more digits shown as "[number not shown]". Only `fin query export` writes the real
number, for the accountant. The original file in the archive, the PDF and its evidence crops show the number as filed,
and so does a file name that contains it. Bank statements loaded before this change hold the number: re-ingest them
(`fin delete <co>`, then load again; that also removes their saved parses). The period of `source_files` is the engagement period (or the stub) holding all the statement's dates
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
reach the window: `sales_cutoff` lists sales invoices within `cutoff_days` (15) of a year end whose service is
on the other side (billed ahead: invoiced by the year end, service starting after it; billed late: invoiced after it,
service ended by it); `purchase_cutoff` lists purchase invoices within `unrecorded_days` (60) after a year end for a
service that ended by it (possible unrecorded liabilities). In both, an invoice dated after the year end for a service
that started by it and ends after it straddles the year end: it is its own line, its amount the part for the service
days up to the year end (by days, in SQL), its note "straddles the year end". An invoice dated after the year end
for a service that started by it and has no service end is flagged in full, its note saying the end is not known. One line per invoice
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
every query; `fin query list` shows `inquiry: <topic>`. `templates/fdd/inquiries.toml` is the standard checklist per topic; a
`templates/inquiries.toml` in the data folder (the user's own list, same format and checks, E910; the client, 2026-11-27) is used
instead when present, and is never committed. `fin query checklist <co> [--json]` first names the list it read, then prints each
topic with its inquiries, their status and answers, and for a topic with no active inquiry (none, or only withdrawn) the list's
questions (none for a topic the list leaves out), which Claude adapts and drafts; fin raises nothing itself. The FDD
report (F8) reads this through `fin.diligence.inquiries.collect(con, engagement_id, topics)`.

FDD orderbook and revenue visibility (F7, `docs/fdd.md`, decisions in `docs/decisions/fdd-f7.md`). `fin ingest <co> <file> --kind orderbook`
(profile `templates/profiles/orderbook.toml`, needs an open engagement) loads the company's orderbook, one row per order: customer (a token, the
registers' token for the same customer), order number, order date, value, currency, start, end and billed to date, as at the date in the title
line ("Orderbook as at 31-Mar-2025"), which must be in an annual period or the stub (E808); `orderbook_lines`, with file + sheet!row. A row with
no customer, order number or value, or a bad date, is E960 and nothing loads; a blank billed cell is NULL (counted as not billed, listed). Orders
in a currency other than INR are loaded with no rupee amount ("not converted (no rate stated as at <date>)") until the user states a rate:
`fin engagement rate <co> USD 84.10 --as-at 2025-03-31` (`fin/diligence/rates.py`, the client, 2026-11-27; table `engagement_rates`, E962 for
a currency, rate or date that cannot be stored) stores rupees per unit for the open engagement, appended with who stated it to
`engagement_rates` and to the audit table `diligence_events` (kind `rate`); `fin engagement rate <co>` lists the rates in use. A rate applies
to the orders in its currency of an orderbook as at the same date (view `orderbook_valued`): value and unbilled value times the rate, rounded
to the paisa, noted "USD converted at <rate> as at <date> (rate stated by the user, #<id>)" in the backlog and the orderbook reconciliation.
Stating a rate again for the same currency and date replaces it from then on. fin never fetches or assumes a rate.
`fin sql --write` refuses `orderbook_lines` and `engagement_rates` (E806).
`fin visibility <co> [--json]` (`fin/reconcile/visibility.py`, view `revenue_visibility` over `backlog_lines`; E961 when no source is loaded)
shows for the 12 months after the last annual period's end, for each of three sources, the items, what is still to come and the part that falls in
that year (straight-line by day over the remaining term), with that part as a percentage of the last period's core revenue: the unbilled
orderbook (value less billed to date, from the later of the start date and the day after the as-at date to the end date; an order with no end date
is in the backlog but not spread; an order whose term ended by the as-at date adds nothing, "term over"); the output and the FDD report give the
orderbook's as-at date and file, and say when the as-at date is not the period end; revenue billed but not yet earned at the period end (core sales register lines invoiced by then whose service
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

Contingencies, commitments, notices and related parties (FDD F5, `docs/fdd.md` F5; decisions in
`docs/decisions/fdd-f5.md`): `fin disclosures load <co> <file> --reading <csv>` (`fin/disclosures/`) stores Claude's
reading of notes to accounts and legal and tax notices for the open engagement in `disclosures` (with
`disclosure_files`: file name, sha256, reading; the same document again replaces its rows), one row per item with
kind, period (a period of the engagement or its stub), counterparty, nature, amount in rupees (the reading's `unit`
applied), forum, stage, assessment year, demand, paid under protest, relationship and page, read_by `ai:claude`. A
counterparty that is a register customer or vendor gets its token on load; one that is a loaded ledger's name (case and
spaces ignored) becomes that ledger; any other is stored as read, as are nature and relationship. E940 for a reading
that cannot be used. `fin disclosures list <co> [--kind]` prints the schedules. Related parties are matched to the
registers by token and to the ledgers by name, a ledger meeting a token through `mask_values` (view
`related_party_matches`); Claude proposes the other links (`fin disclosures related <co>
<file.json>`, E941) and the user confirms or rejects them (`fin review list|confirm|reject <co> --related`; logged in
`mapping_decisions`, scope `related_party`). Views `related_party_transactions`, `related_party_pricing`,
`related_party_loans`. `fin reconcile <co> rpt_vs_books` compares each disclosed transaction (per party and
`books`: sales, purchases, Ledger Vouchers debits or credits on a ledger) with the books; lines at or above the
threshold are for queries, gate G2 requires the run once a reading has one. Golden eval kind `rpt_vs_books`. `fin sql
--write` refuses `disclosure_files`, `disclosures` and `related_party_links` (E806).

FDD net debt and debt-like items (F4, `docs/fdd.md`; decisions in `docs/decisions/fdd-f4.md`): each trial balance
ledger of the latest engagement has a net debt class (cash, debt, debt_like or none), one per ledger for every period
end. fin defaults it from the ledger's confirmed account (view `netdebt_default_accounts`: borrowings, debt
securities, subordinated liabilities and lease liabilities are debt; cash and cash and bank balances are cash; a
profit and loss account is none) and leaves the rest unclassified, other bank balances (deposits, margin money,
lien-marked balances) included, for the user to confirm. A proposal (classes here, provision ledgers too) must name a
ledger of the latest engagement's loaded trial balances; its reason is stored as written. Claude proposes classes with a
reason (`fin netdebt propose <co> <file.json>`, a JSON list of `{ledger, class, reason}`, E930; without a file it
lists the unclassified ledgers) and the user confirms or rejects them (`fin review list|confirm|reject <co> [ids]
--netdebt`, E931). A confirmed class wins over the default; a rejected one leaves the default. Debt-like items not in a
ledger (an indicative gratuity shortfall, a probable contingency, a cut-off item) are proposed with
`fin netdebt item propose <co> --period P --amount N --description ... --evidence <ref>` (signed rupees, positive
raises net debt; a year of the open engagement or its stub; E932) and accepted or
rejected by the user (`fin netdebt item accept <co> <ids>`, `fin netdebt item reject <co> <ids> --reason`, E933;
`fin netdebt item list <co>`). Tables `netdebt_classes` and `netdebt_items` (`db/schema/fdd_netdebt.sql`), refused by
`fin sql --write` (E806); each change is a run event. `fin netdebt show <co> [--period P] [--json]` (E934 without an
engagement) prints the view `net_debt`: per period end (annual periods, then the stub as unaudited management
accounts) cash (debit less credit, so an overdrawn bank is negative cash), debt and debt-like (credit less debit),
debt-like including the accepted items, net debt = debt + debt-like − cash, the proposed items (not applied), and the
ledgers still unclassified (net debt may be incomplete while any is); a period with no trial balance has no ledger
figures (never 0). `net_debt_lines` lists each ledger (file + sheet!row) and item (its evidence). Golden kind
`net_debt` (`ledger`: cash, debt, debt_like, net_debt). The FDD report (F8) reads F4 through
`fin.diligence.netdebt.collect(con, period)`.

FDD findings report (F8, `docs/fdd.md`; decisions in `docs/decisions/fdd-f8.md`): an FDD finding has an area (the
report section: qoe, working_capital, net_debt, revenue, walkthroughs, provisions, tax, contingencies,
related_parties, inquiries, other), a title, an observation, an optional implication, an impact type (ebitda,
net_debt, working_capital, revenue, disclosure, none), a severity (high, medium, low), a deal consideration (price,
spa_protection, condition_precedent, further_work, none) and evidence (file + page, fin output, query). Claude
proposes one with `fin fdd finding propose <co> --area --title --observation --severity --impact --deal --evidence
[--implication] [--adjustment N | --netdebt-item N | --recon N] [--query N]` (E970) and changes or drops it while it is
a draft (`fin fdd finding update <co> <id> ...`, `fin fdd finding drop <co> <ids>`; E971). The amount is never typed:
it is the linked adjustment's or net debt item's amount, a reconciliation run's lines at or above the threshold (sum
of their absolute differences), else the linked query's amount, shown with where it comes from; the linked query's
answer is the management response. Making a finding final snapshots its amount, amount source, link status and
management response; a final finding then shows that snapshot in `fin fdd finding list` and the report, with a flag
"changed since final" and the values now when the live evidence differs; any finding whose linked adjustment or net
debt item is rejected, or whose query is withdrawn, is flagged too. Returning it to draft clears the snapshot. The text is masked and may not hold a typed number or valuation, investment-rating
or buy/don't-buy language (E970). Only the user marks a finding final (`fin fdd finding final <co> <ids>`, or
`--undo` to return it to draft); the gate-guard hook asks the user before any such command. `fin fdd finding list <co>
[--all] [--json]` lists them by severity. Table `fdd_findings` (`db/schema/fdd_findings.sql`, view
`fdd_finding_rows`), refused by `fin sql --write` (E806); every change is a run event.

`fin report <co> --fdd [--drafts] [--render] [--scope S]` builds the FDD report of the latest engagement on the
diligence report's machinery (E873 without an engagement): the first run writes `fdd_narrative.md`, the next lints it
and renders `fdd.html`, `fdd.docx`, `fdd.pdf` and `fdd_data.json` (every value, the references, the gate states and
whether drafts were in). Sections: key findings by severity (each finding with its observation, implication, impact and
amount, deal consideration, management response and evidence); quality of earnings (adjusted EBITDA, bridge,
adjustments, EBITDA to cash); working capital; net debt and debt-like items (F4); revenue, contracts and visibility
(revenue split, contract coverage of the top customers and vendors with lock-in left, notice period and auto-renew, revenue visibility, the recast); walkthroughs (F1; each sample's lines with an exception and the exception lines Claude recorded, labelled not verified);
provisions and payroll (receivables ageing, indicative gratuity with its assumptions; F3); tax (tax charge with net deferred tax, its movement and the MAT credit, the GST returns' totals as filed, and the GST
reconciliations; F6); contingencies and commitments, and related parties (Claude's readings, labelled not verified,
the related parties' transactions in the books, their pricing against other customers and vendors, and their loans with the implied rate; F5); management inquiries and their answers (F2); reconciliations and
queries; basis, gates and scope (with the scope and limitations: no actuarial valuation, leave encashment, legal
review, site visits or MIS/KPI reconciliation); data findings and mappings; the sources appendix. A section whose data
is not loaded says so. Only final findings are in it; `--drafts` adds the drafts and labels the copy a draft. The
narrative may state findings, severity, implications and deal considerations; it never types a number (E602; references
are `{{figure:<name>:<period>}}` with the diligence report's figures plus `net_debt`, `net_debt_cash`,
`net_debt_debt`, `net_debt_debt_like` and `effective_tax_rate`, E874) and has no valuation, investment-rating or
buy/don't-buy language (E972).

### Data-room intake (SPEC-13, `fin dataroom`)

`fin dataroom scan <co> <folder>` reads a whole data room into a plan and loads nothing. It walks the folder and its
sub-folders in name order, leaving out hidden files (a name starting with "."), and lists a link without following
it. A `.zip` archive is unpacked into its own new folder, `dataroom/<co>/<sha256[:16]>/` in the data folder, and its
files (and archives inside it, up to three deep) are part of the plan. Every entry is checked before anything is
written: an archive with an entry whose path is absolute, names a drive or climbs out with `..`, a link entry, a
password, two entries with the same path, or more than `[dataroom] archive_limit_mb` (config.toml, default 2048) of
content is refused and listed, with nothing unpacked; entries are also checked after resolving the path and counted as
they are written. An archive and the archives inside it share one limit (and at most 100,000 entries), so an archive of
archives cannot multiply it. Other archive types (.rar, .7z, .tar, .gz) are listed for the user to unpack. Files with the same
content (sha256) are one plan row; the other paths are its duplicates.

Each file gets a kind from its type and from which column profile's headers it has, the test `fin ingest --kind`
uses: `trial_balance`, `ledger_vouchers`, `registers`, `payroll`, `bank`, `orderbook` or `gst` with the profile;
else `reference_databook` for a workbook with a sheet named like a diligence databook's (quality of earnings, QoE,
EBITDA adjustments, net debt, net working capital, NWC, databook), which loads as a reference (`fin reference add`,
see "Reference documents" below; its source is its path in the data room), never as company data; else `statements` for a workbook with statement sheets or a text PDF fin reads statements from (the test `fin ingest`
uses); else `unknown`. fin does not guess from file names or contents beyond that. The plan shows each row's kind,
profile, the period the file states (the export's header period, or the statements' period ends), its duplicates, and
what will be skipped. A file fin cannot use has a reason and one next step (`fin.dataroom.kinds.REASONS`): a PDF with
no statement page fin reads, such as a ledger PDF ("ask the company for the Excel export, or have the assistant read
it"), another file type, a damaged or protected workbook, a workbook whose headers fit no profile or profiles
of more than one kind, a refused archive. The plan is stored in the company database (`dataroom_files`; the
database is made if the company has none); `fin dataroom plan <co>` shows it again (`--json` for the assistant and
the UI). Scanning again adds only what the plan does not have: a content already in it gains the new path as a
duplicate. An archive refused for its size is tried again, so raising the limit and scanning again unpacks it.

For a file fin could not name, the assistant may propose a kind (`fin dataroom propose`, with a reason; a GST return
needs its profile). The user chooses the rows to load with `fin dataroom approve <co> <ids>` (`--undo` takes it
back); approving a row with a proposed kind confirms that kind, and a file fin cannot use cannot be approved without
one. `fin dataroom load <co>` loads every approved row not loaded yet under the one run id of the command, each
document in its own transaction as `fin ingest` does (statements first, then trial balances before the Ledger Vouchers
checked against them, then the other kinds; source files need the open engagement as with `--kind`), and prints one
outcome per file. A file that fails is recorded on its row (status failed, with the error) and the next file loads;
the command exits 1. A file already loaded (same content) is marked loaded and not loaded twice, so a second scan and
load changes nothing. Gates reopen as on any ingest. A data folder made before this change has no `dataroom_files`
table and no `[dataroom]` table in config.toml: re-create it with `uv run fin init` and re-ingest.

### Reference documents (SPEC-10, `fin reference`)

A banker's or an adviser's databook holds figures for the same company. fin keeps them beside the company's figures,
never as company data: they are in their own tables (`reference_files`, `reference_lines`, `reference_values`; schema
`db/schema/reference.sql`) and never reach `facts`, `current_facts`, `metrics`, statements or reports.
Decisions: [decisions/spec-10.md](decisions/spec-10.md).

- **`fin reference add <co> <file> --source "name" [--unit U] [--reading <csv>]`** reads the file as `fin ingest`
  reads a company file (the same reader, `--unit` for sheets that name no unit, `--reading` for the assistant's
  reading of what fin cannot read), archives it under `archive/<co>/reference/` and stores each figure with its sheet
  and cell (or page), label, period, scope, unit and who read it. The source is required (E980); a file added before
  is refused (E105). It prints the figures, the new lines and the lines waiting for a mapping.
- **Lines**: one per statement, section and label. A label the mapping dictionary knows is mapped at once (status
  `dictionary`); the others wait (`pending`). `fin reference lines <co> [--all] [--json]` lists them. The assistant
  proposes a mapping with `fin reference propose <co> <file.json>` (a list of `{line, account or metric, confidence,
  reason}`, all or nothing): an account of the line's statement, or a metric, in rupees (E981 otherwise). Only the
  user confirms (`fin reference confirm <co> <ids>`, a proposed line) or rejects (`fin reference reject`) a line.
- **`fin reference compare <co> [--all] [--json]`** (view `reference_comparison`): for each figure of a mapped line,
  fin's figure for the same account or metric, period and scope, the difference (fin minus the reference) and both
  sources (fin's file and cell, a stated figure or the metric; the reference's file and cell), in the company's
  display unit (SPEC-06, converted by `display_value`; `--json` has rupees and the `*_shown` values). A missing
  figure is `n/a`. A state per figure: `differs`, `agrees`, `no_fin_figure` or `unmapped` (counted, never shown as zero).
  Differences come first, those at or above the threshold (the open engagement's query threshold, else
  `[diligence] query_threshold`) before the rest, largest first. Without `--all` it lists the differences only.
- **From a difference**: `fin reference query <co> <#id> --question "..."` drafts a query (as `fin query raise`, the
  difference as its amount, both sources in its sources) and `fin reference adjust <co> <#id> --category C
  --description D --amount A` proposes an adjustment with both sources as its evidence (the account when the line is
  mapped to one). A figure that does not differ is refused (E982). Nothing is changed to make the figures tie; the
  user approves the query or accepts the adjustment as for any other.

### Rebuilt statements (SPEC-14, `fin statements --rebuilt`)

The P&L and balance sheet rebuilt from each year's trial balance, and an indirect cash flow derived from them, beside
what the company reported. Decisions: [decisions/spec-14-rebuilt.md](decisions/spec-14-rebuilt.md).

- **What it reads**: `rebuilt_ledgers` (`db/schema/rebuilt.sql`): every ledger row of each loaded trial balance of the
  latest engagement (a newer file of the same period replaces the old one), its closing balance as debit - credit, its
  confirmed account and, while it waits for review, its proposed one. Group rows are not read.
- **`rebuilt_statements`** (temporary view, `fin.rebuilt.view`; in `fin sql` too), scope standalone, the columns of
  `standard_statements` that apply plus `ledgers`, `unmapped_ledgers`, `unmapped`, `incomplete`, `sources` and
  `note`. P&L and balance sheet: each account with a confirmed ledger at or below it, valued as the ledgers' balances
  times the account's side (subtotals are the sum of everything under them); the year's profit also counts in other
  equity, as the balance sheet shows it. `sources` names the trial balance rows behind a line (file sheet!row, the
  first five and a count), and `rebuilt_ledgers` has every one.
- **Missing is not zero**: a ledger with no confirmed account is never rolled up. One waiting for review marks the
  totals above its proposed account incomplete; one with no proposal marks every total of its year. Each such line
  says how many unmapped ledgers are under it and their amount (debit - credit), and a memo line "Unmapped ledgers"
  under the P&L and the balance sheet carries the year's whole unmapped amount (a balance, so not under the cash
  flow, whose lines are all marked incomplete). A year with no confirmed ledger has no cash flow totals.
- **Cash flow (indirect)**, for a year whose previous trial balance (ending the day before it starts) is loaded; the
  first year has none, and says so. Operating: profit for the year, depreciation added back, and the change in each
  working capital account; investing: the change in fixed assets less depreciation (purchases net of disposals),
  investments and other bank balances; financing: borrowings, share capital, leases, and the change in other equity
  less the year's profit ("Other changes in equity"). Opening cash plus the three activities equals closing cash;
  any difference (an unbalanced trial balance, unmapped ledgers) is an "Unmapped / other" line. Income taxes paid,
  finance costs paid, interest and dividends received, sale proceeds of fixed assets and investments, dividends
  paid and the exchange rate effect are "not derivable" with the reason and where the amount sits instead, never
  estimated (Q11; `fin.rebuilt.view.NOT_DERIVABLE`). Which balance sheet account goes
  to which line: `fin.rebuilt.view.CASH_LINES`.
- **`fin statements <co> --rebuilt [--period] [--json]`**: each line's reported (the standalone `standard_statements`
  figure), rebuilt and difference (rebuilt - reported, in SQL; blank when either side has no figure), in the
  display unit (each column converted in SQL), with a note after a line that is incomplete or not derivable; the latest fiscal year by default.
  A reported "Unmapped / other" line is shown with the rebuilt side blank, and an account reached only by ledgers
  that are not confirmed has a blank rebuilt figure marked incomplete. Then what to ask the company for: a year with no trial balance, or one with group totals only. `--scope
  consolidated` is refused (the rebuild is standalone). E822 when the company has no engagement, the period is not
  one of its periods, or nothing (or not the period asked for) can be rebuilt.
- **`fin export`** adds "Rebuilt P&L", "Rebuilt Balance sheet" and "Rebuilt Cash flow" sheets in the databook
  layout (caption with the unit, reported, rebuilt and difference per period, and a Notes column) when a year can be
  rebuilt.
- **Statements screen** (web UI): the statements as reported (the databook, SPEC-06), or reported vs rebuilt (the
  same three columns, each rebuilt line's trial balance rows, and the notes).
- **Nothing reported changes**: a difference is for a query or an adjustment with evidence.

### Discrepancies (SPEC-15, `fin discrepancies`)

Every difference the reconciliations found, in one place, from the headline down to the invoice. Decisions:
[decisions/spec-15.md](decisions/spec-15.md).

- **`discrepancies`** (temporary view, `fin.discrepancies.view`; in `fin sql` too): one row per line that differs
  (kinds `difference`, `left_only`, `right_only`, `fx`) in the latest run of each reconciliation of the latest
  engagement, at four levels: headline (`tb_vs_statements`: trial balance against the statements; `service_recast`:
  the recognition difference, below), account
  (`gl_vs_tb` per ledger, `payroll_vs_gl`), party (the sales and purchase registers' customer and vendor lines) and
  transaction (their invoice lines, and `bank_vs_invoices`' match, receipt and invoice lines). Each reference figure
  that differs from fin's (`reference_comparison`, SPEC-10: fin minus the reference) is a headline row too, with id
  `ref-<value_id>` and no parent or children; a figure fin does not hold is not a row. Every amount is in the
  statements' sign, lower rung minus the rung above it. An account or party row is under the headline row of the same
  period for its account (or the nearest account above it); an invoice row is under its party row. A row with no parent
  row is a root (bank lines). `explained` is the sum of the rows under it and `unexplained` the difference less that;
  a foreign-currency line without a rupee value has a blank difference (missing is not zero), and so has its parent's
  explained amount. Each row carries its two sources, note, threshold, whether it is over the threshold, its query and
  status, and whether a query can be raised on it.
- **Recast against the books** (SPEC-17 item 5, decisions in [decisions/spec-17.md](decisions/spec-17.md)): per annual
  period that `fin recast run` ran for, the period's recast line is a headline row keyed `revenue` and labelled
  "recognition difference" (column `label`): books (the sales register by invoice date) against recast, difference =
  books - recast, the recast's stored sides swapped into that sign. The recast's schedule lines (each invoice whose
  recast and booked amounts in the period differ) are transaction rows under it, so they explain it in full. Month
  roll-ups and undated invoices are not rows. A period with no register line dated in it shows the books missing and no
  difference (missing is not zero, nor a full-amount difference). Each side names its register rows (file sheet!row).
  These rows are not queried from here: the period's difference becomes a
  `recast` adjustment (`fin recast run`). The CLI, the sheet's Kind column and the screen show the label instead of
  the kind.
- **`fin discrepancies <co> [--level headline|account|party|transaction] [--over-threshold] [--json]`**: the rows ranked
  by the size of the difference, in the display unit (converted in SQL). E803 without an open engagement.
- **`fin export`** adds a "Differences" sheet when there are differences (one row per difference, with the line it is under and its query).
- **Differences screen** (web UI): the tree, headline first, each row with its two sides, difference, explained and
  unexplained amounts, sources and query status; a unit picker and "over the threshold only". Ticked rows that can be
  queried (one period) open the chat with a request to draft one query from them (`fin query raise --items`); the user
  approves it on the Queries screen. Ticked reference rows ask for one query each (`fin reference query`). E008 for a
  row that cannot be queried, rows of two periods, or reconciliation lines and reference rows ticked together.
- **Nothing is fixed**: a difference is the company's, for a query or an adjustment with evidence.

## Display units and the databook (SPEC-06)

- **Display settings** (`config.toml [display]`): `unit` (rupees, thousands, lakhs, millions or crores),
  `decimals` (0 to 4), `negatives` ("minus": -1,234.5; "brackets": (1,234.5), and a zero as a dash) and
  `unit_label` (the caption every table names its unit with). The default is millions, one decimal, brackets,
  "INR Mn" (the client, answer 14). `[display.companies.<name>]` shows one company differently, with any of the
  same keys (a unit without a label gets that unit's label: INR, INR '000, INR lakh, INR Mn, INR Cr). E002 for an
  unknown key or a value out of range.
- **Converted in SQL only**: the DuckDB macro `display_value(v, rupees_per_unit, decimals)` (`fin.display`) divides
  exactly (integer arithmetic) and rounds half away from zero; Python and templates only format what SQL returned (a
  unit test searches the code for a division). Each figure is rounded on its own, so a total is the rounded total; a
  subtotal whose rounded lines do not add up to it is marked * with a note under the table. Earnings per share are
  rupees per share, two decimals, never scaled. % of revenue (P&L) and growth on the same period a year before are
  computed in SQL too.
- **`fin statements <co> --layout databook`**: per scope and statement one table, line items as rows and periods as
  columns (fiscal years first, then quarters and half years, oldest first), subtotals marked =, rounded totals *;
  `--of-revenue` and `--yoy` add those columns per period. `--unit` shows one run in another unit (E001 for an
  unknown one). The default layout (`blocks`) is one block per period, as before; both name the unit in each
  header, and a stated or corrected figure keeps its mark.
- **`fin export`**: each statement sheet is the databook: row 1 a caption with the company, statement, scope and
  unit, row 2 the headings, panes frozen below them and right of the labels, subtotals bold with a line above,
  "Unmapped / other" in italics, the converted value in each cell with the display's number format (for example
  `#,##0.0;(#,##0.0);"–"`), and each figure's source (file and sheet!cell or page) and marks in a cell comment.
  Money metrics on the Metrics sheet are in the display unit too.
- **Statements screen** (web UI): pickers for scope, statement, periods (fiscal years, quarters and half years, or
  all), unit, % of revenue and YoY; the databook table with a sticky label column; each figure opens a drawer with
  its source, its stored rupee value and its marks (stated, corrected, unconfirmed mapping, read by AI); **Export to
  Excel** runs `fin export` as a job and the file can be downloaded. A picker value not offered is E001.
- **Reports, evidence pages and dashboards** use the company's display settings: money in the report as "1,340.9 INR
  Mn" (narrative references stay inline references), the evidence page's figure titles add the value in the display
  unit after the stored rupees, `fin engagement bridge` and the diligence and FDD reports show the display unit, and
  the dashboards' statements table is in it.

## Actor (SPEC-02)

Who took an action is passed in by the interface, never read deeper down: the web UI passes `user`; the CLI passes
`ai:claude` when Claude Code runs it (`CLAUDECODE` is set) and `user` otherwise (`fin.actor`). It is stored as `user`
or `ai:<provider>`: `mapping_decisions.actor`, `engagement_rates.actor` (proposals are always `ai:claude`; `on_request` is true when an AI
confirmed, edited or rejected because the user asked), the `actor` argument of every decision's run, and `read_by` of
what an AI read (`ai:claude`: facts from a reading, contracts, walkthroughs, disclosures). Status, eval counts, evidence
and the listings test the `ai:` prefix, and output names `ai:claude` "Claude". Data loaded by an earlier fin stored
`claude` and `human`: re-create the data folder (`fin init`) and re-ingest; an audit database without the `jobs`
table is refused as older (E002).

### Figures the user states (SPEC-08)

A document can lack a line the metrics need (a P&L with no finance cost line, so EBITDA is not computed). The user
states the figure; fin never invents one.

- `fin fact state <co> --account A --period P --scope S --value V --reason "..."` (`fin/facts.py`, CLI
  `cli/facts.py`) stores it in the company table `stated_figures` with `read_by = 'user'` (the figure is the user's),
  the actor who ran the command (`fin.actor`: `user`, or `ai:claude` from Claude Code on the user's word), the reason
  as the user typed it (not masked), the run id and the time. The value is rupees with at most two decimals (E407 otherwise, or
  for an unknown account, scope or period, an empty reason, or a figure already stated for the account, period and
  scope: withdraw it first). Allowed inside a diligence engagement too (Q3).
- A stated figure never replaces a document's. When a loaded document already gives the account, period and scope,
  nothing is stated: a finding is recorded (check `stated_figure`, `document_gives`, with both values and the
  document's cell), and no figure changes. When a document that gives it is loaded later, the document's figure is
  used and the stated one stays listed, not in use.
- `current_facts` adds the stated figures no loaded document gives (`stated_id` set, no `fact_id`), so the metrics,
  `metric_inputs`, statements and reports use them. The view `stated_now` lists the stated figures and whether each
  is in use.
- `fin fact unstate <co> <id>` withdraws one (E408 for an unknown or withdrawn id); `fin fact list <co> [--json]`
  lists them. Both commands back up the company database, log a run and an `audit.stated_figures` row, and refresh
  the analytics.
- Marks: `fin statements` adds "(stated by the user, #id)" to the line and the Excel export puts it in the cell's
  comment after the source;
  `fin sql` gives a row with a `stated_id` the source "stated by the user", the reference "stated #id: reason" and
  `source_read_by = 'user'`; the evidence page has a section per stated figure with its reason; `fin audit <co>
  <metric> <period>` names the stated input; the report's data coverage lists them under "Figures the user stated"
  with their reasons, and its appendix names each by id; gate G1 lists them; `fin status` and the Home screen count
  them.
- A metric without a value says why: "EBITDA not computed for FY24 standalone: no finance_cost for FY24. Load a
  document that has it, or state it (...)" (`fin/metrics/missing.py`), in `fin engagement bridge` and
  `fin audit <co> <metric> <period>`; "a denominator is zero" when no input is missing.
- `fin sql --write` refuses any statement that names `facts`, `raw_values`, `stated_figures` or
  `fact_corrections` (E505), before the backup: figures come from documents (`fin ingest`, `fin review`), from the
  user (`fin fact state`), or from a correction of fin's own misread (`fin fact correct`).
- **Correcting a figure fin read wrongly**: `fin fact correct <co> <fact_id> --value V --reason "..."`, only on the
  user's word. It backs up the company database, sets `facts.value` (rupees; rupees per share for EPS), keeps the
  cell or page and the printed text in `raw_values`, and records the value before, the reason, the actor (`fin.actor`:
  `ai:claude` with `on_request` when Claude Code ran it, else `user`, as for mapping decisions) and the run in the company table
  `fact_corrections` and the audit table of the same name. Held documents and the fact's own document are checked
  again, and the analytics refreshed. A correction is for fin's own misread, never to make figures tie: inside an
  open diligence engagement a figure under a query (through a finding on it) or an adjustment (through a finding, or
  the same account, period and scope) that is not closed, withdrawn or rejected is refused (E409), as are an unknown
  or superseded fact, a value that is not a number, the value it already has, or an empty reason. The view
  `corrected_now` has the correction in force per fact (loaded and held documents).
- Corrected figures are marked where stated ones are: `fin statements` "(corrected, correction #id)" and an Excel
  comment; `fin sql` provenance "<cell>, corrected #id from <value before>"; the evidence page and `fin audit <co>
  <fact_id>` give the value before and the reason; the report's data coverage lists them under "Figures corrected"
  and its appendix names the correction; gate G1 lists them (passing) and has them as an input, so a correction
  reopens it; `fin status` and the Home screen count them. `fin fact list` lists stated and corrected figures.
- A data folder made before this has no `stated_figures` table: re-ingest into a new data folder.
- Readings behind these choices: `docs/decisions/spec-08.md`.

Questions (SPEC-07):
- `fin schema [co] [--table name] [--json]` lists the views meant for questions (`current_facts`, `fact_sources`,
  `standard_statements`, `metrics`, `metric_inputs`, `metric_definitions`, `document_facts`) with each column and
  type read from the database (the company's, or an empty one made from the schema files), one line of meaning and an
  example query (the `-- about:` and `-- example:` comment lines above each view in its schema file).
- When `fin sql` (read or write) fails on a column that does not exist, E504 lists the columns of the tables named
  in the query, the question views that have a column of that name, and points to `fin schema`.
- `fact_sources` joins a fact to its file (`source_file`), `source_ref`, sheet, cell, page, printed `label` and
  `section`, `source_confirmed`, `source_read_by` and `document_status`: `JOIN fact_sources USING (fact_id)`.
- Saved metrics: `fin metric add <name> --formula ... --unit ratio|rupees|days --definition "..."` saves a metric for
  one company (`--company`) or for every company (also copied into a company created later). The formula has
  accounts, metrics (built-in or saved before it), numbers, `+ - * /` and brackets; a name that is both a metric and
  an account is the metric. It is computed in SQL with the built-in metrics (in DOUBLE, rounded once: rupees to 2
  places, ratio and days to 6); a missing input or a zero divisor gives NULL. It is annual when it uses an
  annual-only metric, else for every period. It is in `metrics` and `metric_definitions` with category `user`, its
  inputs in `metric_inputs` (an account as printed; a metric's own inputs), and so in the Excel export, the analytics
  tables and the dashboards; the analytics are refreshed after each change. `fin metric list [co]` shows them;
  `fin metric remove <name> [--company co]` removes one (E507 while another saved metric uses it). Both back up the
  databases they change. `fin sql --write` refuses to change the saved metric tables (E504). E506 when a metric
  cannot be saved.

### Diligence in the UI (SPEC-09)

Builder's readings: [decisions/spec-09.md](decisions/spec-09.md).

- **`fin adjust import <co> <file> [--sheet]`** proposes one adjustment per row of a walk sheet (.csv, .xlsx, .xlsm; the
  first sheet unless `--sheet`). The first filled row is the header: `Period`, `Scope`, `Category`, `Description`,
  `Revenue effect` and/or `Cost effect` (at least one column, and a value in each row), optionally `Account` and
  `Source reference` (any case; `_` reads as a space). The EBITDA effect is revenue effect minus cost effect.
  Each adjustment's evidence is the file and the row's description cell (`walk.xlsx Walk!G5`), then the source
  reference. Every row is checked as `fin adjust propose` checks it before any is added, so one bad row, an unknown
  column or a missing sheet is E847: the message names the row (or the column or sheet), nothing is imported and no event is logged. A sheet
  already imported into the engagement is E847 too (a row's evidence is already an adjustment that is not rejected):
  importing it twice would count each adjustment twice. The file is archived like an ingested document; one run, one `adjust import` event and one event per adjustment.
- **`adjusted_pl`** (view, `fin sql`): the P&L of the latest engagement's annual periods, reported and adjusted per
  account, period and scope (every annual account; the Bridge screen shows the P&L lines, and a balance sheet or
  cash flow account is there as reported with adjustment 0). Accepted adjustments land on `revenue` and `total_income` (revenue effect),
  `total_expenses` (cost effect), `profit_before_exceptional_items`, `profit_before_extraordinary_items` and `pbt`
  (the EBITDA effect), and a
  reclassification moves `moved_amount` from `from_account` to `account`. Other lines are as reported. Proposed
  adjustments are not applied. A line with no reported figure has no adjusted figure.

## Web UI (optional, `fin ui`)

Plan and owner decisions: [web-ui-plan.md](web-ui-plan.md); builder's readings: [decisions/web-ui.md](decisions/web-ui.md). The UI is built in four steps; all four steps (foundation, read-only screens, decision buttons and uploads, chat) are here. The CLI does not change, and without the add-on nothing else does either.

- **Look**: Inter (variable woff2, OFL) and Basecoat are vendored, so the UI works offline. Every screen has the same anatomy: a page header (title, one-line description, the primary action when there is one), then Basecoat cards (header with title and description, the table or form below; a wide table scrolls inside its card, never the page). Status values are Basecoat badges with one tone each (`fin.ui.format`: ok, warn, bad, neutral). Timestamps read "26 Nov 2026, 13:18"; figures are right-aligned with tabular numbers; long text is cut to one line and opens on click; a run's problems are one badge ("13 warnings") that opens the grouped list. The top bar has the company select and the tabs, with the number of things waiting on Files, Mappings, Queries and Gates. Checked in Chromium and WebKit at 1366, 1440 and 1920 wide.
- **Install**: `./install.sh` (by default; `--no-ui` leaves it out) or `uv run fin ui setup` (`uv sync --frozen --extra ui`: FastAPI, Jinja2, uvicorn, python-multipart for uploads; the chosen PDF add-on is kept). No frontend build and no npm: pages are HTML rendered by the server, with htmx and the Basecoat component CSS and JS (shadcn/ui as plain HTML classes) vendored under `src/fin/ui/static/vendor/` (versions and licences in `VENDOR.md`). `fin parser use`, `fin update` and `install.sh` keep the `ui` extra once installed. `fin doctor` has a `web ui` line (installed or not; never a failure).
- **`fin ui`**: starts the server on 127.0.0.1 (default port 8765, `--port`), prints and opens a link with a random token, stops on Ctrl-C. Without the extra it stops with E006 and says how to install it; a busy port is E006 too.
- **Local only**: the server binds 127.0.0.1; a request with another Host, or without the token cookie (HttpOnly, SameSite=Strict, set from the link's token), is refused with E007; no CORS headers; no API documentation pages.
- **Screens**: a top bar with the company picker and the navigation (SPEC-11): **Home**, then four Basecoat dropdown menus, Data (Files, Mappings, Statements), Analysis (Questions, Reports, Dashboards), Diligence (Engagement, Queries, Gates, Bridge, Findings) and System (Settings, Health, Log). Each item is a link; the open screen is `aria-current="page"` and its group's button is highlighted; a group's button shows the number of things waiting in its screens. A chat panel is on the right. The chosen company stays in the link of every screen, including Home, and changing the picker reloads the same screen for that company. Every screen shows what fin's own modules return; a page is `/<screen>?company=<name>` and its partial `/partials/<screen>?company=<name>`.
  - Home: what `fin status` returns: counts, periods and gaps, the next gate, and what waits for the user (each row links to its screen).
  - Files (docs/specs/SPEC-03): **Ingest**, **New company**, the upload form, the Jobs card, one row per file and the findings on filed data (open and accepted, as `fin findings list`); while PDF pages were skipped and no add-on is chosen, the PDF add-on offer (above). A row is a document (its current version: loaded or held, with its figures, unmapped labels, figures read by the PDF add-on and figures read by AI, marked "read by AI, not checked"), a file that failed (the code, message and run_id ingest wrote to `review/<company>/<file>.error.json`) or an inbox file not loaded yet (waiting). A failed row shows its code; a held row the codes of the open findings that hold it. A failed or held row, and a document with statement pages fin did not read, shows one next step (`fin.services.next_steps`: a text for every ingest error code): a file held for its unit (E203) asks "Which unit is this file in?" with one button per answer (lakhs, crores, millions, thousands, rupees): it starts the job **Load with the unit**, which loads its `review/` copy as `fin ingest <company> <file> --unit <answer>` does (the unit stored as given by the user); no tables or no text layer (E201, E104) offer **Read with the assistant** and **Choose a PDF add-on**; an unsupported type (E103) says to save it as .xlsx or a PDF with text. While a job runs, the rows ask for themselves every second, so each file's outcome shows as soon as ingest has it; the rest of the page stays usable. **New company** (also on the empty screen before any company exists) takes the company's name and a short code (lower-case letters, digits, `-` and `_`; its folders and database are named after it): a run "company create" makes the database and `inbox/<code>/`, stores the name (table `company_profile`, shown on the Files screen) and opens its Files screen; a code in use or not valid, or an empty name, is E008.
  - Files, data room (SPEC-13): a folder on this computer (**Scan folder**) or a `.zip` (**Upload and scan**: saved in its own folder under `dataroom/<company>/uploads/`, `.zip` only, the upload limits below; E008 otherwise) is scanned as a job, as `fin dataroom scan`. The plan shows each file fin can use (kind, profile, period, duplicates, approved or not, or its load outcome) and what will be skipped with the reason and next step. **Approve** and **Undo approval** are the user's decision per row (`fin dataroom approve [--undo]`); **Load approved** loads the approved rows as a job, as `fin dataroom load`.
  - Mappings: the mapping items waiting for review (label, a sample value as printed, statement, section, proposed
    account, confidence, reason, where it is), then every other review kind `fin review list` has (Tally ledgers,
    customer sectors, tax ledger classes, related party links, net debt classes, bank matches), each with its rows
    as the CLI prints them, or the reason it has none (E803 without an open engagement).
  - Statements: the statements as reported in the databook layout with scope, statement, periods and unit pickers and a source drawer (SPEC-06), or reported vs rebuilt from the trial balance (`?view=rebuilt`; SPEC-14); why there is nothing to show when there is not. Another view, or a picker value not offered, is refused with E001.
  - References (SPEC-10): the comparison of `fin reference compare --all` (counts, each figure with both sources, the state and whether it is at or above the threshold), the lines waiting for a mapping with **Confirm** (a proposed line) and **Reject**, and the reference files. A difference offers **Draft a query** and **Propose an adjustment** while an engagement is open (both need one), and a waiting line **Ask for a proposal**: each opens the chat with the request, because the UI never drafts.
  - Engagement: open an engagement (periods, threshold, stub, credit terms; as `fin engagement open`) when there is none; else its terms, query and adjustment counts, the gates (each links to the Gates screen) and **Close** (as `fin engagement close`).
  - Queries: the open engagement's accountant queries and adjustments with status and evidence; **Raise a query** (amount, period, scope, where the figures are, the question; as `fin query raise`), **Edit** a draft's wording, **Record answer** on a sent query (as `fin query answer`), **Export workbook and email** and **Export Word briefs** (as `fin query export [--brief]`; the files are written to `exports/<co>/<run_id>/`, fin sends nothing), and the walk-sheet import (as `fin adjust import`).
  - Gates: each gate's state, why it reopened, its checklist and what it unlocks (as `fin gate status`). A failing line links to the screen that closes it (a `fin query`, `fin adjust`, `fin review`, `fin findings` or `fin fdd finding` command in the line), or offers **Ask Claude** when no screen does. An E801 refusal anywhere links to the gate it names.
  - Bridge (money in the display unit, SPEC-06): reported to adjusted EBITDA per period and scope, one step per accepted adjustment with its status and evidence, and the adjustments not applied (proposed, rejected) apart with the proposed sum (as `fin engagement bridge`); `?view=pl` shows `adjusted_pl` side by side (reported, adjustment, adjusted) and the balance sheet and cash flow as reported, with a note that adjustments change only the P&L. Another view is E001.
  - Findings: the engagement's FDD findings (not dropped) with status, severity, amount and flags.
  - Reports: **Health report**, **Diligence report** and **FDD report** (each a job, as `fin report <company> [--diligence|--fdd]`: the first run writes the narrative stub, which Claude fills in chat; the next run checks it and renders), **Export to Excel** (a job; as `fin export`), the narratives under `reports/<company>/` with their section headings (the rendered report adds the findings and data coverage), and the HTML, Word, PDF and Excel files under `reports/<company>/`; HTML opens in the browser as a sandboxed page (it cannot use the UI's token), the others download. A name that is not one of the listed files is refused with E001.
  - Questions: the questions asked of the company's data and saved (`fin sql --question`; read runs with a result), newest first, the latest 100: question, SQL, rows, when. **Open evidence** shows the evidence page (sandboxed, as a report), **Download** saves it, **Run again** is a job that runs the same SQL again with a new evidence page (a run "sql" with `rerun_of`). **Ask a question** puts `/ask` in the chat box: Claude writes the SQL. An id the company does not have is E001.
  - Dashboards: **Build the dashboard** (a job, as `fin dashboard --build`: `fin.dashboard.build()`) and the last good build in a frame. The Evidence site is built under the base path `/dashboard-site` (`dashboards/evidence.config.yaml`), which is where the UI serves `<data>/dashboard/build`; the dev server (`fin dashboard`) is at `http://localhost:3000/dashboard-site`. A page outside the build is E001.
  - Settings (opens without a company): the data folder, the display settings the company's numbers are shown with (unit, decimals, negatives, caption: `[display]`, or the company's own `[display.companies.<name>]`; read-only here, the Statements screen picks another unit for a view), config.toml's PDF add-on (or why config.toml cannot be read), and **Install** for each PDF add-on (the add-on job, as `fin parser use`). Other AI providers are on hold (SPEC-16).
  - Health (opens without a company): `fin doctor`'s checks, each with its fix, and **Update fin** (a job, as `fin update`: refused when doctor fails afterwards, with its problems).
  - Log: the company's latest 100 runs (newest first) with who ran each (the run's `actor`, or "not recorded") and their problems, filtered by command and by actor; a figure's trail (`fin audit`) by fact id, or by metric and period (E504 for a fact id that is not a number or a figure the company does not have).
  - Queries, Gates, Bridge and Findings say so (E803, or nothing loaded) when the company has no open engagement.
- **Decisions (step 3)**: the buttons are the user's decisions, each through the service the matching CLI command calls (`fin.services.decisions`), in a run with that command's name (run record, audit log, backup, gate checks, masking, refusals). The run's arguments record `via` (`ui` or `cli`) and `actor`; apart from `via` the run record is the CLI's. A button press is always the user's (`actor` `user`), even when `fin ui` was started from inside Claude Code. The alert shows the lines the CLI prints. The UI never drafts or proposes; Claude does that in chat (step 4). After a button the screen is shown again with an alert: "Done", or "Not done: <code>" with the CLI's message and the run_id.
  - Mappings: Confirm (the proposed account, company scope; held documents are checked again and the metrics refreshed, as `fin review confirm`), Use account (another account, searched over the dictionary's accounts of the statement, as `--account`) and Reject (`fin review reject`). One button confirms every proposal at confidence 0.8 or above; a lower one is marked and confirmed on its own. The other review kinds have Confirm, Use account / sector / tax class where the CLI has that choice, and Reject (`fin review confirm|reject <co> <ids> --<kind>`).
  - Files: Save as is on an open finding (`fin findings accept`); on a finding that names a mapping, Change the mapping (choose another account: `fin map set` with the label and section as the dictionary stores them, same run record). **Delete** a document, in a dialog that says what goes: a run "document delete" (`fin.documents`) copies the company database and the document's files to `backups/<run_id>/`, deletes every version of the file (the documents with its sha256) with their rows in every company table with a `doc_id` (raw values, facts, notes, findings, verify runs and lines) in one transaction, removes its archived original, notes, a reading no other document uses and the inbox copy with the same content, appends a `deletions` row and rebuilds the mapping items waiting for review (`pending.json`) and the shared metrics. A gate whose inputs changed reopens. A doc_id the company does not have is E001. `fin document delete <co> <ids>` does the same from the command line.
  - Files, as jobs: **Ingest** loads `inbox/<company>/` (a run "ingest", as `fin ingest <company>`; files loaded already are skipped, E105); **Re-ingest**, in a dialog, loads a document again from its archived original as a new version (as `fin ingest --force`, with the reading it was loaded with): the earlier version and its findings are superseded. A doc_id that does not exist is E008 before the job starts.
  - Files: **Read with the assistant** starts a chat turn at once whose message names the file (`review/<company>/<file>` for a failed one, the inbox copy for a loaded one with pages not read), why fin could not read it, and the command that loads the reading (`fin ingest <company> <file> --reading <csv>`, with `--force` for a loaded file); it is offered only while the inbox copy of a loaded file is there. Claude writes the reading and loads it; the row then says how many figures were read by AI, "not checked": whether they are trusted stays the user's decision. **Choose a PDF add-on** puts `/parser` in the message box.
  - Home: the checks that did not run, with why and what would make each run (as `fin status`).
  - Queries: Approve a draft, Mark sent (a dialog: only if you sent it; fin sends nothing), Close an answered query (dialog), Withdraw (dialog with the reason, as `--reason`); adjustments: Accept, Reject (dialog with the reason). They refuse as the CLI does (E805, and E801 while a gate is not approved).
  - Gates: Approve in a confirmation dialog (`fin gate approve`; refused with E802 while the checklist fails or an earlier gate is not approved).
  - Findings: on a draft, **Edit** (title, observation, implication, evidence, area, impact, severity, deal consideration: `fin fdd finding update`; E970 for a field at fault, such as a typed figure; a form with no field filled is E008) and **Drop** in a confirmation dialog (`fin fdd finding drop`); Mark final in a confirmation dialog, Return to draft (`fin fdd finding final [--undo]`; E971). Only a click marks a finding final.
  - Raise, edit, answer, export and import (above) are the user's own forms, recorded with `actor` `user` and `via` `ui` like the buttons.
  - Not in the UI: proposing an adjustment one at a time, and the other proposals (Claude does those in chat). Every command's place is in "Where each command is" below.
  - Every change is a `POST` that needs the token cookie and an `Origin` header naming the server (else E007, nothing runs). A missing reason, an unknown action or a bad id is E008 and starts no run.
- **Chat (step 4)**: the panel on the right of every screen runs the installed Claude Code headless (`claude -p --output-format stream-json --verbose --include-partial-messages`) in the repo folder, so CLAUDE.md, skills and the client's hooks apply (never `--bare`, never a bypass mode), with `FIN_DATA_DIR` set to the UI's data folder. It uses the user's own Claude Code login; the UI warns when `ANTHROPIC_API_KEY` is set (Claude Code would bill that key) and is for one person on one machine, not for hosting or sharing.
  - One `claude` process per conversation at a time (E011 while one runs); each turn is a process (`--session-id` first, `--resume` after), so a conversation resumes after `fin ui` restarts. Conversations are JSON files in `<data folder>/chat/`; the list and **New chat** are in the panel header. **Stop** ends the process. Text streams in over server-sent events (htmx SSE extension); tool calls show collapsed; errors show with their code.
  - The user's decisions are denied to Claude with `--disallowedTools` (`fin gate approve`, `fin fdd finding final|draft`, `fin query approve|sent|close|withdraw`, `fin adjust accept|reject`, `fin review confirm|reject`, `fin findings accept`, provisions confirm/reject, net debt item accept/reject, each also as `uv run fin ...`); Claude points to the button. Any other request that is not already allowed is shown in the chat with **Allow once** (the next turn gets `--allowedTools` for exactly that command or file) or **Deny**.
  - **Ask Claude** buttons (every screen, and rows of Mappings, Queries and Findings) and the skill chips `/ask`, `/ingest`, `/diligence`, `/report`, `/status` put text in the message box; the user sends it. Claude missing or not startable is E009, not logged in is E010.
- **Jobs**: a long action runs as a background job, so the page answers at once. One worker thread runs the jobs one at a time in order; a later one is shown as queued. Each job is a row of `jobs` in audit.duckdb (action, arguments, state, the last progress line, result lines, code, run_id, times); the Jobs card (Files, Reports, Questions, Dashboards, Settings and Health) asks for itself every second while a job is queued or running, and a reload shows the same jobs. States: queued, running, done, refused (the action's error code and message), failed (E012, the run has the traceback), interrupted (the server stopped before it ended: marked at the next `fin ui`; start it again). `POST /job` needs the token cookie and the Origin (E007); an action that is not a job is E008. The jobs: Export to Excel, Make a report, Install a PDF add-on (Files and Settings; the service `fin.services.addon`, which `fin parser use` calls too), Ingest, Re-ingest, Load with the unit, Scan a data room and Load the approved files (`fin.services.dataroom`, which `fin dataroom` calls too), Build the dashboard, Update fin and Run a question again (each job's run records `via` `ui` and `actor` `user`; Install a PDF add-on and Update fin also run before any company exists, from Settings and Health once `fin init` has run); `POST /job` takes `item` for Re-ingest (the doc_id), Load with the unit (the held file's name), Run a question again (the query_id) and Scan a data room (the folder, which must exist), and `choice` for Install a PDF add-on (the add-on), Load with the unit (the answer) and Make a report (health, diligence or fdd); anything else is E008. While a job writes, a page that reads the same database waits for it (as for another command; E501 after the retries).
- **Uploads**: the Files screen saves .xlsx, .xlsm, .xls and .pdf files into `inbox/<company>/` of the chosen company (a run "ui upload", one event per file). The name must be plain (letters, digits, space, `. _ - ( ) &`; a path is cut to its last part; no leading dot, no `..`), at most 50 MB per file and 100 MB per request, and it never overwrites a file already in the inbox; all files are checked first, so a refusal (E008) saves none; before any company exists the upload is refused (E008). The button does not ingest: the screen says the files are waiting; **Ingest** loads them.

### Where each command is

Every `fin` command, with the screen that does it, "chat" (Claude runs it in the chat panel; the user's decisions are denied to it there) or "terminal only" with the reason (SPEC-11). `tests/unit/test_command_coverage.py` fails when a command has no row.

| Command | Where |
|---|---|
| `fin adjust accept` | Queries |
| `fin adjust import` | Queries |
| `fin adjust list` | Queries |
| `fin adjust propose` | chat |
| `fin adjust reject` | Queries |
| `fin analytics` | chat |
| `fin audit` | Log |
| `fin contracts coverage` | chat |
| `fin dashboard` | Dashboards |
| `fin dataroom approve` | Files |
| `fin dataroom load` | Files |
| `fin dataroom plan` | chat |
| `fin dataroom propose` | chat |
| `fin dataroom scan` | Files |
| `fin disclosures list` | chat |
| `fin disclosures load` | chat |
| `fin disclosures related` | chat |
| `fin discrepancies` | Differences |
| `fin delete` | terminal only: deletes a whole company; it asks for the company's name typed in the terminal |
| `fin doctor` | Health |
| `fin document delete` | Files |
| `fin engagement bridge` | Bridge |
| `fin engagement close` | Engagement |
| `fin engagement open` | Engagement |
| `fin engagement passthrough` | chat |
| `fin engagement rate` | chat |
| `fin engagement status` | Engagement |
| `fin engagement terms` | chat |
| `fin eval bundle` | terminal only: the golden evaluation runs on the client's own machine from its runbook (docs/client-handoff.md), not in the UI |
| `fin eval init` | terminal only: the golden evaluation runs on the client's own machine from its runbook (docs/client-handoff.md), not in the UI |
| `fin eval run` | terminal only: the golden evaluation runs on the client's own machine from its runbook (docs/client-handoff.md), not in the UI |
| `fin export` | Reports |
| `fin fact correct` | chat |
| `fin fact list` | chat |
| `fin fact state` | chat |
| `fin fact unstate` | chat |
| `fin fdd finding drop` | Findings |
| `fin fdd finding final` | Findings |
| `fin fdd finding list` | Findings |
| `fin fdd finding propose` | chat |
| `fin fdd finding update` | Findings |
| `fin findings accept` | Files |
| `fin findings list` | Files |
| `fin gate approve` | Gates |
| `fin gate status` | Gates |
| `fin ingest` | Files |
| `fin ingest-batch` | chat |
| `fin init` | terminal only: makes the data folder before anything else runs; install.sh runs it |
| `fin map accounts` | chat |
| `fin map customers` | chat |
| `fin map ledgers` | chat |
| `fin map propose` | chat |
| `fin map set` | Files |
| `fin map tax` | chat |
| `fin metric add` | chat |
| `fin metric list` | chat |
| `fin metric remove` | chat |
| `fin netdebt item accept` | terminal only: the user's decision, denied to Claude in chat, and no screen has it yet |
| `fin netdebt item list` | chat |
| `fin netdebt item propose` | chat |
| `fin netdebt item reject` | terminal only: the user's decision, denied to Claude in chat, and no screen has it yet |
| `fin netdebt propose` | chat |
| `fin netdebt show` | chat |
| `fin parser show` | Settings |
| `fin parser use` | Settings |
| `fin payroll confirm-unit` | chat |
| `fin provisions confirm` | terminal only: the user's decision, denied to Claude in chat, and no screen has it yet |
| `fin provisions ledgers` | chat |
| `fin provisions propose` | chat |
| `fin provisions reject` | terminal only: the user's decision, denied to Claude in chat, and no screen has it yet |
| `fin provisions show` | chat |
| `fin query answer` | Queries |
| `fin query approve` | Queries |
| `fin query checklist` | chat |
| `fin query close` | Queries |
| `fin query edit` | Queries |
| `fin query export` | Queries |
| `fin query list` | Queries |
| `fin query raise` | Queries |
| `fin query sent` | Queries |
| `fin query withdraw` | Queries |
| `fin recast contracts` | chat |
| `fin recast run` | chat |
| `fin reconcile` | chat |
| `fin reference add` | chat |
| `fin reference adjust` | chat |
| `fin reference compare` | References |
| `fin reference confirm` | References |
| `fin reference lines` | References |
| `fin reference propose` | chat |
| `fin reference query` | chat |
| `fin reference reject` | References |
| `fin report` | Reports |
| `fin review confirm` | Mappings |
| `fin review list` | Mappings |
| `fin review reject` | Mappings |
| `fin sample draw` | chat |
| `fin sample show` | chat |
| `fin schema` | chat |
| `fin sql` | Questions |
| `fin statements` | Statements |
| `fin status` | Home |
| `fin ui` | terminal only: starts the web UI itself |
| `fin ui setup` | terminal only: installs the web UI itself |
| `fin update` | Health |
| `fin verify` | chat |
| `fin visibility` | chat |
| `fin walkthrough record` | chat |
| `fin walkthrough summary` | chat |
