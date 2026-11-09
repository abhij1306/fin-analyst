# Financial Health Analysis Toolkit — Build Plan

Working name: `fin-analyst` (repo), `fin` (CLI). Rename freely.

## Phase status

| Phase | Name | Status |
|---|---|---|
| 0 | Repo, install, guardrails | Done |
| 1 | Foundation: config, audit, errors, DB access | Done |
| 2 | Schema and dictionary | Done |
| 3 | Synthetic fixtures, Excel ingestion, batch | Done |
| 4 | PDF bake-off and PDF ingestion | Done |
| 5 | Mapping and review | Done |
| 6 | Validation gates | Done |
| 7 | Metrics and analytics export | Done |
| 8 | Query and evidence | Done |
| 9 | Reports | Done |
| 10 | Dashboards | Done |
| 11 | Audit, delete, update | Done |
| 12 | Release | Done, v1.0.0 (Claude Code clean-machine test on macOS pending) |

After 1.0.0 (not phases): real annual-report PDFs, then quarterly and half-year results. Every fact now has a
period (`FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`; see §7). Results statements (SEBI Reg 33) ingest from PDF next to
annual reports, quarter / half-year / year columns land in their own periods, and like periods are compared
(E404). Quarterly metrics: revenue, PAT, EBITDA, margins, year-on-year and quarter-on-quarter growth. The
health report is annual, with a short "latest quarters" section; the dashboard company page shows quarters.

Claude readings (after 1.0.0, not a phase): fin's deterministic extraction stays the primary path; the optional
Docling add-on (below) is the automatic fallback for PDF pages pdfplumber skips. Only when the user asks, Claude Code
reads pages neither could read (scanned, E2xx, skipped statement pages) as images and writes a reading file (`fin.extract.reading`: one CSV row per figure, as printed, with the
original page or cell). `fin ingest <co> <file> --reading <csv>` loads it for the statements and scopes neither fin nor
Docling read, through the same stage, gates, commit. Facts carry `facts.read_by` (`fin` / `docling` / `claude`) and
`facts.verified`.
`fin verify <co> <file> --reading <csv>` compares an independent reading with what is loaded (agree / differ /
missing in fin / missing in reading) and marks agreeing facts verified (a Claude-read fact only by a reading other
than the one it was loaded from). It compares the value fin uses now (`facts.value`, so a figure corrected with
`fin sql --write` is checked as corrected) with the reading's figure converted to rupees with the reading's own unit
(EPS as printed), exactly. It works on loaded and held documents alike (on a held document, a reading tells whether
a finding is a misread). Every disagreement is a finding, not a failure: it is shown to the user, kept with the
document in the company `findings` table and recorded in `audit.findings` (check `verify_reading`), and it neither
holds nor releases the document: a loaded document stays loaded, a held one stays held. Verify exits 0 and never
changes a figure. Unverified
Claude- or Docling-read facts still feed metrics; `/ask` flags them and the data quality page counts them.

Docling PDF fallback (after 1.0.0, not a phase; owner decision 2026-11-07): an opt-in add-on, not a required
dependency. `./install.sh --docling` installs the `docling` extra (`uv sync --extra docling`: Docling 2.148, PyTorch,
about 1.4 GB with its models; not on Intel Macs, where PyTorch has no wheels), downloads Docling's models
(`docling-tools models download layout tableformer rapidocr`, into `~/.cache/docling/models`) and records
`[pdf] docling = true` in `config.toml` (`fin init --docling`); `--no-docling` syncs without it and records false; a
second `install.sh` and `fin update` keep what `config.toml` says. A `config.toml` with no `[pdf]` table has not
opted in. The pipeline for every PDF is: pdfplumber (`fin.extract.pdf`, unchanged) -> Docling on only the statement
pages pdfplumber skipped (E201-E204) -> anything still unread stays unread, as before. Docling runs once per document
on a PDF of just those pages (OCR and table structure on; one converter per process, so a batch loads the models
once), and `fin.extract.docling_pages` reads its table grids with fin's own rules and helpers: header rows give each
column's period end, length and scope ("Standalone" / "Consolidated" spanning the columns, else the page title), the
label is the text columns left of the first period column (a "Note" column kept as the note), headings are sections,
the unit is the page's unit line, and the same guards apply (two columns with the same period and scope: E202; no
unit: E203; misread figures: E204; no usable table: E201). Bboxes are converted to fin's top-left points, so evidence
crops work. Facts it reads have `read_by = 'docling'` and are unverified until `fin verify` agrees, like Claude's;
the outcome and log say how many figures Docling read and from which pages. When no page could be read by
pdfplumber, Docling still tries before the file fails. A scanned PDF (E104) of at most 40 pages (a results filing) is
OCR'd by Docling and its statement pages found by fin's title rules; a longer scanned PDF stays E104. Docling failing
is a warning (E206) and leaves the pages unread; it never fails a document. With the add-on off or not installed,
pages stay unread and the run logs one notice per document naming them and the two ways to read them
(`./install.sh --docling`, or a Claude reading). With a reading, the order is fin -> Docling -> the reading fills the
statements and scopes neither read. Chosen from a benchmark on real NSE filings: on 19 pages pdfplumber skipped,
Docling found 99-100% of the figures of Claude's readings, at about 15 s a page on CPU.

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
unverified Claude-read facts and inbox files not yet ingested.

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
  with sources; data coverage (`statement_coverage` view: mapped share per statement and unverified Claude-read
  figures; `documents.unread_pages`: statement pages fin could not read; unconfirmed mappings; missing data for the
  years and quarters shown); and appendices (condensed standard statements: every printed subtotal, marked † when
  its mapped lines do not add up to it; metric definitions; facts behind the figures shown, for both scopes and the
  quarters, each line of a summed value listed, per-share facts unscaled; peers' facts are in their own reports).
  Narrative references accept quarter and half-year periods (`{{metric:ttm_revenue:FY26-Q2}}`).
- Dashboards: `analytics.duckdb` also holds metric categories and definitions, the standard statements (in the
  documents' unit), findings, coverage, unread pages and unverified Claude-read facts. Company page by category
  (trend charts, any metric, quarters, statements for a chosen period, findings, coverage); compare page (category,
  then metric, across companies); data quality page (findings, held documents, coverage, unread pages, unverified
  figures). An analytics file missing a table is made again at the next refresh (it is derived).

Simpler PDF parser (after 1.0.0, not a phase; owner decision): the product is the analysis, not the parsing. The
parser reads clean, standard digital statements; a page it cannot read goes to the Docling add-on when it is
installed, and what is still not read is listed and a Claude reading fills it. Each parsing rule was kept only if it reads a statement correctly in a document it was not written for, or
if removing it would load a wrong figure; document-specific workarounds were removed (CHANGELOG "Simplify the PDF
parser"). A page whose figures look misread (a row label holding a figure, a figure with an unpaired bracket) is
not read (E204), and so is an untitled page carrying on a page that was not read. Standalone and consolidated
columns printed side by side are not read (E202).

Diligence (D1) (after 1.0.0, not a phase; plan: [docs/DILIGENCE_PLAN.md](docs/DILIGENCE_PLAN.md), build tracker:
[docs/DILIGENCE_BUILD.md](docs/DILIGENCE_BUILD.md)): a diligence engagement on one company, beside the statement
layer, which is unchanged. New tables in each company database (`db/schema/diligence.sql`, run after
`company_views.sql`; greenfield, no migrations): `engagement` (annual periods in scope, query threshold, open or
closed; at most one open), `accountant_queries` (draft → approved → sent → answered → closed, or withdrawn before
closed; raised from a finding, copying its period, scope, difference and sources without changing it, or by hand)
and `adjustments` (signed effect on EBITDA in rupees for a period of the engagement, a scope and a category from
`config.toml [diligence] adjustment_categories`, an optional dictionary account, and evidence: a query, a finding or
a source reference; proposed → accepted or rejected). Every row added and every status change is appended to the
audit table `diligence_events`. `config.toml` gets a required `[diligence]` table (`query_threshold`, default
100000.00 rupees; `adjustment_categories`). Commands: `fin engagement open|status|close`, `fin query
list|raise|edit|approve|sent|answer|close|withdraw`, `fin adjust propose|list|accept|reject`; every one but
`engagement open` needs an open engagement (E803); an invalid adjustment is E804; an unknown id or a status change
not allowed is E805. fin never sends a query: `fin query sent` records that the user sent it. Rules in `CLAUDE.md`
("Diligence") and `/diligence`. Views (`db/schema/diligence_views.sql`, run after `diligence.sql`; also in `fin sql`),
on the latest engagement (open or closed) and its annual periods: `adjusted_ebitda` (reported EBITDA, exactly the
`ebitda` metric, plus accepted adjustments; proposed ones summed beside it, not applied; margins), `ebitda_bridge`
(reported EBITDA, one step per accepted adjustment by id, adjusted EBITDA) and `ebitda_to_cash` (EBITDA, non-cash and
other, working capital change, taxes paid, other operating, CFO, capex, free cash flow, cash conversion on reported
and adjusted EBITDA; the lines add up to CFO exactly; a missing input makes the lines using it NULL, and a taxes-paid
line not mapped is 0 only when CFO equals cash generated from operations). `fin engagement bridge <co> [--period]
[--scope] [--json]` shows them (money in the documents' unit as `fin statements`; JSON in rupees); E803 when the
company has no engagement. Gates (`fin/diligence/gates.py`, a registry in code, approved in order): G1 Data
(every annual period in scope has a loaded document with figures for it, no held documents, no mapping items
waiting for review; its inputs include the figures, every `current_facts` value by account, period and scope, so a
changed figure reopens it), G2 Queries (no query in draft; its inputs are each query's id, wording and whether it is a draft, so a new or
edited query reopens it and sending, answering or closing one does not; unlocks `fin adjust accept|reject`), G4 Adjustments (no
proposed adjustment, every accepted one has evidence; unlocks `fin engagement close`); G3 and G5 come later.
`fin gate approve <co> <gate>` appends a row to `gate_approvals` (company database) with the sha256 of the gate's
inputs, and a `gate` row to `diligence_events`; it refuses (E802, exit 3) while the checklist fails or an earlier gate
is not approved. A gate is approved while its inputs still hash to the latest approval's and every earlier gate is
approved, reopened when they changed (`fin gate status` says which input), open when never approved. A command whose
gate is not approved refuses (E801, exit 3), checked in the same transaction as its change. `fin status` shows an
open engagement's queries by status, proposed adjustments and gate states. Only the user approves a gate: the
PreToolUse hook `gate_guard.py` asks the user before any Bash command that looks like a gate approval (§8). The
diligence tables (`engagement`, `accountant_queries`, `adjustments`, `gate_approvals`) change only through `fin
engagement|query|adjust|gate`: `fin sql --write` refuses any statement other than a SELECT that names one (E806,
exit 2; nothing is run, the refusal is logged in `queries`). Every diligence command that changes data first backs
up the company database to `backups/<run_id>/`.

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
| PDF fallback (optional) | Docling 2.148 (`docling` extra) | Pages pdfplumber skips, and scanned results filings (OCR); opt-in, `./install.sh --docling` |
| Notes / auditor text | pdfplumber → Markdown (`fin.extract.text`) | AnyDoc and pdf-inspector are MIT; not needed (Phase 4 decision) |
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
  PLAN.md  CLAUDE.md  CHANGELOG.md  LICENSE
  Brewfile  install.sh
  pyproject.toml  uv.lock  .python-version  .gitignore
  .claude/
    settings.json        # hooks
    commands/            # ingest, ingest-batch, review, ask, statements, report, dashboard, audit, status, verify
    hooks/               # log_action.py, gate_guard.py
  src/fin/
    cli.py  config.py  paths.py  ingest.py  statements.py  workbook.py
    audit/     logger.py  errors.py
    db/        conn.py  schema.py  schema/{audit,dictionary,analytics,company,company_views}.sql
    extract/   excel.py  pdf.py  docling_pages.py  docling_run.py  text.py  units.py  periods.py  reading.py
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
- `metrics`, `metric_definitions`, `metric_input_accounts` (views) and `metric_inputs` (metric → fact_ids lineage)
- `standard_statements` (temporary view per connection, `fin.statements`): the standard P&L, balance sheet and cash flow with coverage
- Diligence: `engagement`, `accountant_queries`, `adjustments` (tables) and `engagement_periods`, `adjusted_ebitda`, `ebitda_bridge`, `ebitda_to_cash` (views on the latest engagement)

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
  it records the exit code the command returns.
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
  doubt it asks. It prints nothing for any other command or for input it cannot read, so it never blocks anything
  else. `fin sql --write` cannot write gate approvals (E806, below). Every other data change goes through `fin`, which
  backs up and records it, without a prompt (the general guard hook was removed on 2026-11-07).

**Traceability**

- `fin audit <company> <metric> <fy>`: metric → facts → mapping decisions → raw values → file + sheet!cell or page, plus the ingest run.
- `fin audit <company>`: full history of ingests, reviews, queries, reports, failures. A run belongs to the company
  when its args name it or an audit row names it (events about `<company>/<file>`, mapping decisions, validation
  results, findings, queries, reports, deletions, diligence events), so a batch ingest stays after the company is
  deleted.
- Lineage of a summed value: `current_facts.fact_ids` (and `standard_statements.fact_ids`) list every printed line
  behind it. `fin sql` with a `fact_ids` column gives every line's source ("sum of N sources: ..."), confirmed or
  verified only when every line is; the evidence page shows each line. Traces show a per-share fact in rupees per
  share.

## 9. Error handling

The code table (meaning and what to do) is in `README.md`, "Error codes"; each code's class
is in `src/fin/audit/errors.py`. E1xx-E2xx fail one document (the batch goes on); E301-E303 load facts unconfirmed;
E401-E405 are findings (the document is held); setup, database and output errors stop the command.

**Rules**

- No silent failures, no bare `except`, no default values filling gaps.
- One bad file never stops a batch.
- Each document loads in one transaction: stage → validate → commit (held when a check finds something), or roll
  back entirely on an error.
- What a company filed that does not add up is a finding, never a failure. Only nothing readable (E1xx/E2xx) fails.
- Tolerances live in `config.toml`, never hard-coded.
- Exit codes: 0 success, 1 completed with failures, 2 command error, 3 refused (needs confirmation).

## 10. Install, update, uninstall

**Install (end user)**

```
git clone <repo-url> && cd fin-analyst
./install.sh             # or ./install.sh --docling for the optional Docling PDF add-on
claude
```

`install.sh` (idempotent, logs to `logs/install.log`):

1. Check macOS and architecture.
2. Install Homebrew if missing (asks first).
3. `brew bundle --file=Brewfile` (uv, node, quarto, poppler); tried up to 3 times, a minute apart, since GitHub
   release downloads sometimes fail for a minute or two.
4. `uv sync --frozen` (with `--extra docling` and the Docling model download when `--docling` is given or
   `config.toml` already has `[pdf] docling = true`; `--no-docling` removes it).
5. `npm ci --prefix dashboards`
6. `uv run fin init [--docling | --no-docling]` — creates data folder, config, databases from the schema files, and
   records the Docling choice in `config.toml`.
7. `uv run fin doctor` — checks every dependency, version, folder permission, that the databases exist and are not from an older fin.

**Update:** `fin update`

1. Refuse if repo has local changes.
2. Back up all databases to `backups/<run_id>/`.
3. `git pull`, `uv sync --frozen` (with `--extra docling` when `config.toml` has the add-on on), `npm ci`.
4. Refresh the metrics export.
5. `fin doctor`. If it fails, `fin update` prints each failed check (code and fix) and the doctor's own run id, with the update's run id.

**Uninstall:** delete the repo. The data folder is never touched by uninstall.

## 11. Commands

| CLI | Claude Code | Who |
|---|---|---|
| `fin init`, `fin doctor` | — | Human |
| `fin ingest <co> [path] [--force]` | `/ingest` | Either |
| `fin ingest <co> <file> --reading <csv>` | `/ingest` (offered after a failure or skipped page) | Claude, when the user asks |
| `fin ingest-batch [folder]` (default `inbox/`) | `/ingest-batch` | Either |
| `fin verify <co> <file> --reading <csv>` | `/verify`, `/ingest` | Either |
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
| `fin adjust propose\|list <co>` | `/diligence` | Claude proposes |
| `fin adjust accept\|reject <co> <ids>` | `/diligence` | Either (when the user says; needs gate G2 approved) |
| `fin gate status <co> [--json]` | `/diligence` | Either |
| `fin gate approve <co> <gate>` | `/diligence` | Human (Claude only when the user says; the PreToolUse hook asks) |

Metrics are views in each company database; `shared/analytics.duckdb` is rebuilt after every ingest, data change
and update.

## 12. Testing and CI

- **Synthetic fixtures** (`tests/fixtures/sample-financials/`): three fake companies (Kestrel Forgings: Excel with formulas; Bluepeak Polymers: Excel per year, crores, older wording, standalone + consolidated; Tarang Industrial Services: text PDF with notes), known values in `answer_key.json`, plus deliberately broken files (see Phase 6). The set already exists at `sample-financials/` (README, `answer_key.json`, `generator/`, `clean/`, `broken/`), local and untracked; Phase 3 moves it into `tests/fixtures/` and commits the generated files, so CI needs no LibreOffice.
- **Private golden set**: one real company, ~30 hand-checked figures, kept in the data folder; run with `pytest --golden`. Never committed.
- **CI** (`.github/workflows/ci.yml`, Linux, every PR and main push): ruff, pytest, `shellcheck install.sh`; one job ingests the synthetic companies once, renders a report (Quarto) and builds the Evidence dashboard (node 24). A `docling` job syncs the `docling` extra, restores Docling's model cache and runs the tests
marked `docling` (real Docling on a synthetic PDF); the other jobs run without the extra. `macos.yml` (main and on
demand): `install.sh` twice, `fin doctor` and a smoke ingest; and `install.sh --docling`, the Docling tests and
`--no-docling`.
- **Releases**: semantic version tags, `CHANGELOG.md` written at release.

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
- Audit logger (runs, events, JSONL), error classes with codes (README error table), exit codes
- `db/conn.py`: short-lived connections, read-only by default, retry with backoff on lock
- One schema file per database, created by `fin init` (no migrations)

Done when:
- A test command writes `runs`, `events`, and JSONL
- A forced error logs its code and returns the right exit code
- Lock test retries then raises E501
- `fin init` is idempotent and never alters an existing database

### Phase 2 — Schema and dictionary
Build:
- Schema files for company and shared databases (§7), in `src/fin/db/schema/`
- Schedule III seed CSV and loader
- Label normalization (case, punctuation, "Rs."/"₹", whitespace, note references)

Done when:
- `fin init` creates every shared database from its schema file
- Synthetic fixture labels map through the dictionary

### Phase 3 — Synthetic fixtures, Excel ingestion, batch
Build:
- Move `sample-financials/` to `tests/fixtures/sample-financials/` and commit the generated files (§12); its `generator/` is the fixture generator
- Excel extractor: sheet/cell capture, header and period detection, unit detection ("₹ in lakhs", "in crores"), multi-year columns
  - Each column's period length and scope come from the nearest heading above it ("Quarter ended", "Standalone"; a heading covers the columns it is merged over, or the blank cells right of it when typed once; a trailing "(Unaudited)" / "(Audited)" / "(Reviewed)" is ignored), else the sheet's one scope; a flow column naming no length on a sheet with interim columns, or naming no scope on a sheet naming both, is E202
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
- Notes and auditor report sections → Markdown in `notes/`, each page's content after a `<!-- page N -->` line

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
- Caveat: the extractor was written against these two synthetic reports, so its 100% is an upper bound; the
  real-PDF run below tested it.
- Licenses (the repo is MIT): pdfplumber MIT, pdfminer.six MIT, pypdfium2 Apache-2.0/BSD-3-Clause, pdf-inspector
  MIT (Firecrawl; Rust wheels for macOS arm64/x86_64), AnyDoc (`firecrawl-anydoc` on PyPI; the `anydoc` name is an
  unrelated Slack bot) MIT. PyMuPDF (AGPL) is not used. pdf-inspector is a dev dependency, for the bake-off only.
  Update (2026-11-07, owner): licence is no longer a constraint (AGPL or private is fine); Docling (MIT) was added
  as the optional fallback for the pages pdfplumber skips (see "Docling PDF fallback" at the top).
- Notes and the auditor's report go to Markdown with pdfplumber too (bold lines become headings, note tables become
  Markdown tables), so AnyDoc is not needed.
- Real-PDF run (after 1.0.0): six Indian annual reports were tried. The word-position approach holds once header
  blocks, column bands, per-page scope and page skips are in place. Four reports load, and their main totals match the
  PDF for both years. One fails E403 because of a cash mapping, which is correct for that mapping. One is scanned with
  an unusable text layer. Quarterly PDFs fail with E202. Details are in CHANGELOG `[Unreleased]`.

### Phase 5 — Mapping and review
Build:
- Dictionary lookup → `review/<co>/pending.json`
- `fin map propose` accepts Claude JSON proposals (label, account, confidence, reason)
- `fin review list|confirm|reject`: confirm (optionally with another `--account`), reject; global vs company scope
- `/ingest` command: runs ingest, then Claude proposes mappings for pending labels

Done when:
- Unmapped labels flow propose → confirm; dictionary grows
- `fin review confirm` run by Claude asks the user first and records `actor=claude (on user request)` in `mapping_decisions`
- `facts.confirmed` reflects review state

`fin review confirm|reject <ids>` is all or nothing: every id is checked before anything is written. After a
confirmation, held documents and every document whose facts it mapped are checked again (a loaded one that now fails
a check, such as a subtotal that could not run while a line was unmapped, is held), and analytics are refreshed.

After 1.0.0 (real annual reports): `fin map set` maps a label to an account for one company without a pending item,
which is how a document a gate rejected gets fixed (its `.error.json` lists the lines each failed check used, the
unmapped labels and the commands); then the `review/` copy is ingested again. `fin map accounts` lists the accounts.
Lookup prefers the company's own mappings, and a row under a sub-heading ("Financial liabilities") is also looked up
under the balance-sheet part it is in (from the last part heading, or the last total line). Stored keys are
normalized again when read, so a normalization change does not orphan an existing mapping.

New accounts through review: a proposal may name an account that is not in the dictionary (`new_account`: parent,
sign, unit, description; checked like a seed row, E304 otherwise). `fin review confirm` adds it to `accounts`
(`accounts.source` 'seed' or 'review', `run_id`) and maps the label in one transaction; `--name`
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
closing cash. E403 compares only mapped accounts, never label text: `closing_cash` = balance-sheet `cash`, or, when the
cash flow prints the bank overdrafts netted in closing cash (Ind AS 7; memo account `cf_overdraft_in_cash`, "Bank
overdraft" under "Cash and cash equivalents as above"), `cash` less those overdrafts. An E402 finding lists the lines summed,
each with its signed value and cell or page (`other_source`).

### Phase 7 — Metrics and analytics export
Build:
- SQL views: revenue growth; gross, EBITDA, PAT margins; CFO/EBITDA; free cash flow; capex/revenue; receivable, inventory, payable days; cash conversion cycle; debt/EBITDA; interest cover; current ratio; debt/equity; ROCE; ROE
- `metric_inputs` lineage
- Export to `analytics.duckdb` after every ingest

Done when:
- Synthetic metrics equal precomputed expected values exactly

### Phase 8 — Query and evidence
Build:
- `fin sql` (read): rejects anything but one SELECT without `--write` (E502), returns JSON with provenance columns, logs to `queries`
- `fin sql <co> --write --sql "…"`: applies a user-requested data change to one company DB. Refuses BEGIN/COMMIT/ROLLBACK, ATTACH/DETACH, COPY FROM DATABASE and LOAD/INSTALL before anything runs (E504). Backs up first, runs in one transaction, records the statement and rows affected in the audit log, and refreshes metrics
- `fin sql --evidence`: HTML page with highlighted PDF crops and Excel context grids
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
- Update on a test clone backs up the databases

### Phase 12 — Release
Build:
- README for the end user: install, daily workflow, troubleshooting by error code
- Clean-machine test: new macOS user account, clone → first report using README only
- Tag `v1.0.0`

Done when:
- Clean-machine test passes without help
