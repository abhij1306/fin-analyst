# fin-analyst

A local tool for looking at the financial health of companies. You drop financial documents (Excel, PDF) into a
folder. `fin` loads them into a local database, answers questions with the source cell or page for every number, and
produces reports and dashboards, all inside [Claude Code](https://claude.com/claude-code).

**What it does**

- Loads Excel and text-based PDF documents (annual reports, and quarterly or half-year results), one database per
  company, and checks them (balance sheet balances, subtotals add up, cash flow ties to cash, figures agree across
  documents for the same period, units make sense). What does not add up is shown to you as a finding; you decide
  whether to keep the document as filed or have Claude fix it.
- Computes ratios and growth in SQL and traces every number back to its file and cell or page.
- Writes health reports (HTML, Word, PDF) and an Evidence dashboard.
- Logs every action, so any number, report or change can be audited.

**What it does not do**

- It analyses only. It gives no scores, rankings or recommendations; decisions stay with you.
- It does not read scanned PDFs (no text layer) itself and does not guess: unknown labels and missing numbers are
  flagged, never filled in. When you ask, Claude can read pages fin could not (see "Pages fin could not read").
- Everything stays on your Mac. The Python code never calls an AI service; Claude Code does the judgement work.

## Install

You need a Mac (Apple Silicon or Intel), an internet connection for the first install, and Claude Code.

```sh
git clone https://github.com/abhij1306/fin-analyst.git
cd fin-analyst
./install.sh
```

The installer checks you are on macOS, asks before installing [Homebrew](https://brew.sh) if it is missing, installs
`uv`, `node`, `quarto` and `poppler` (lets Claude read PDF pages as images), sets up the Python environment, creates your data folder and runs `uv run fin doctor`.
You can run it again at any time; it only installs what is missing. Its log is `~/FinAnalysis/logs/install.log`.

To keep your data elsewhere, set `FIN_DATA_DIR` (for example `export FIN_DATA_DIR="$HOME/Documents/FinAnalysis"`)
before installing, and add that line to `~/.zshrc`.

Check the installation at any time with `uv run fin doctor`. Each line says `ok`, `warn` (works, but worth fixing,
such as poppler missing) or `FAIL`, with an error code and the fix (see Troubleshooting).

## Daily workflow

Start Claude Code in the `fin-analyst` folder with `claude`. Then:

| Step | You do | What happens |
|---|---|---|
| 1 | Drop files in `~/FinAnalysis/inbox/<company>/` (lower-case company name, for example `kestrel`) | |
| 2 | `/ingest <company>` (or `/ingest-batch` for every folder under `inbox/`) | Loads the files, checks them, and proposes accounts for labels it does not know. A document with findings is held and Claude asks you: save as is, or have Claude fix it (see "Findings"). A file with nothing readable goes to `review/<company>/` with a reason. A file already loaded is skipped (E105). |
| 3 | `/review [company]` | Lists the proposed mappings. Tell Claude which to confirm or reject. When no account fits a line, Claude may propose a new account; it is added only if you confirm it (`fin map accounts --added` prints added accounts to share). |
| 4 | `/ask <company> <question>` | Answer, calculation, source table (file and sheet!cell or page), the SQL, an evidence page and flags (unconfirmed mappings, missing data). |
| 5 | `/statements <company> [period] [scope]` (or `uv run fin statements <company>`) | Shows the profit and loss, balance sheet and cash flow in the standard account order, whatever labels the filing used, in the unit the documents use (lakh, crore, ...). A printed subtotal that its mapped lines do not fully explain gets an "Unmapped / other" line for the difference and says how much the lines explain. `uv run fin export <company>` writes `reports/<company>/<company>_financials.xlsx`: every period side by side, one sheet per statement and scope, and a Metrics sheet with each metric's definition. |
| 6 | `/report <company> [--peers a,b]` | Writes a narrative that describes the numbers (it never types them) and renders HTML, Word and PDF into `reports/<company>/<scope>/`. See [Reports and dashboards](#reports-and-dashboards). |
| 7 | `/dashboard` (or `uv run fin dashboard`) | Refreshes the dashboard data and starts the Evidence dashboard at http://localhost:3000 (company, compare and data quality pages; see [Reports and dashboards](#reports-and-dashboards)). It reads a copy of the data, so you can ingest while it is open. `uv run fin dashboard --build` makes a static site instead (kept in `~/FinAnalysis/dashboard/build`; a failed build, E603, keeps the last good one). |
| 8 | `/audit <company> [metric fiscal-year or --fact id]` | Traces a figure to its source, or lists the company's history. |
| 9 | `/verify <company> <file>` | On request: Claude reads the pages you name and `fin verify` compares that reading with what is loaded (agree, differ, missing). |
| any time | `/status [company]` (or `uv run fin status`) | Per company: periods loaded and gaps, held documents, findings, mappings waiting for review, unverified figures read by Claude, and inbox files not yet ingested. |

Until you confirm a mapping, the facts it affects are marked unconfirmed and are left out of metrics. Answers say so.

Metrics (`/ask` uses them; definitions in the `metric_definitions` view and the export's Metrics sheet):
profitability (gross, EBITDA, EBIT, PBT and PAT margins), returns (ROE, ROCE, ROA, on closing and on average
balances), liquidity (current, quick, cash ratio), solvency (debt/equity, net debt, net debt/EBITDA, interest cover,
debt/assets), efficiency (asset turnovers, receivable, inventory and payable days, cash conversion cycle), cash flow
(CFO, free cash flow, CFO/EBITDA, CFO/PAT, capex/revenue, capex/depreciation), growth (year on year, 3- and 5-year
CAGR), earnings quality (other income and exceptional items over PBT, effective tax rate), EPS as reported, lender
metrics for NBFCs and banks (net interest income, cost to income, credit cost, assets/equity), and trailing twelve
months from four consecutive quarters. A metric whose inputs are missing has no value; nothing is guessed.
EBITDA is computed from profit before exceptional items, so a one-off exceptional item does not move it.

### Reports and dashboards

The health report (`/report`) is organised by metric category:
- **Summary:** key metrics (revenue, growth, EBITDA and margins, PAT, ROCE, ROE, debt/equity, current ratio, cash
  from operations, free cash flow) for the latest five fiscal years, standalone and consolidated where both exist.
- **One section per category** with data (profitability, returns, liquidity, solvency, efficiency and working
  capital, cash flow, growth, earnings quality, per share, lending): a trend table and a chart.
- **Latest quarters** (when quarterly results are loaded): revenue, EBITDA, PAT, margins, growth on the year
  before and on the previous quarter, and the last four quarters (TTM).
- **Peer comparison** (`fin report <company> --peers a,b`): key metrics side by side for the latest fiscal year
  every company has.
- **Findings** (open and accepted, with their sources) and **data coverage**: the mapped share of each statement,
  figures read by Claude not yet verified, pages fin could not read, unconfirmed mappings and missing data.
- **Appendices:** the condensed standard statements (subtotals), metric definitions and formulas, and every fact
  behind the metrics with its file and cell or page.

The narrative is Claude's; every number in it is a reference filled in from the database, and it describes
without judging.

The dashboards (`/dashboard`): **Company** shows one company by category (trend charts per category, any metric,
quarters, the standard statements for a chosen period, its findings and coverage); **Compare** picks a category
and a metric and shows it across companies per fiscal year, with its definition; **Data quality** shows findings,
held documents, coverage, unread pages and unverified Claude-read figures for every company.

The slash commands call `fin` for you. You can also run `uv run fin --help` and use the commands directly.

### Changing data

Ask Claude in plain words, for example "set the FY24 revenue of kestrel to ...". Claude uses
`uv run fin sql <company> --write --sql "..."`, which backs up the company database to `backups/<run_id>/`, runs the
statement in one transaction and records it in the audit log.
`fin query` is read-only and refuses anything but one SELECT.

### Findings

fin is an analysis tool: when what a company filed does not add up, that is a finding to show you, not a reason to
reject the document. The checks: the balance sheet balances (E401), subtotals equal their lines (E402), closing cash
ties to the balance sheet (E403), a figure equals the same figure in other documents (E404, for example a
restatement, or a quarter against its year) and is not off by a unit factor (E405). `fin verify` differences are
findings too.

A document with open findings is **held**: it is stored, but its figures stay out of answers, metrics and reports
until you decide. `uv run fin findings list [company]` shows each finding with this document's figure and what it was
compared with, each with its file and page or cell. Claude then asks you:

- **Save as is**: `uv run fin findings accept <company> <finding ids>`. The document is loaded exactly as filed. The
  findings stay attached, marked accepted: `/ask` flags them and the report describes them.
- **Have Claude fix it**: for example a label mapped to the wrong account (`uv run fin map set ...`, then
  `uv run fin ingest <company> <file> --force`), or a figure read wrongly (`uv run fin sql <company> --write ...`).
  Held documents are checked again after every fix; a finding that no longer appears is resolved, and a document
  with no open findings is loaded.

Example: in old-format reports "Cash and bank balances" also holds bank deposits, so the cash flow's closing cash is
smaller (E403); mapping that line to `cash_and_bank_balances` (not compared with closing cash) fixes it.

### Pages fin could not read

A scanned PDF (E104), a file with no readable headers (E201-E204), or a statement page fin skipped can be read by
Claude instead, only when you ask. `/ingest` names the pages and offers it. Claude reads the pages as images and
copies every figure exactly as printed into a reading file in `review/<company>/`; then
`uv run fin ingest <company> <file> --reading <reading.csv>` loads it for the statements fin could not read, through
the same checks. These facts are marked as read by Claude and stay unverified
until a second, independent reading agrees (`uv run fin verify <company> <file> --reading <second.csv>`).
Differences are findings, not errors: shown with both values and sources for you to decide (see "Findings"). `/ask` flags unverified Claude-read figures,
and the dashboard's data quality page counts them.

### Deleting a company

Ask Claude, or run `uv run fin delete <company>` and type the name when asked (Claude uses `--yes` when you have
asked it to). Everything removed is copied to `backups/<run_id>/` first. The audit history stays, and so do mappings
you confirmed for all companies.

## Where your data lives

Outside this folder, in `~/FinAnalysis` (or `FIN_DATA_DIR`). Updating or uninstalling never touches it.

```
~/FinAnalysis/
  config.toml             # display unit, default scope, fiscal year start, validation tolerances
  inbox/<company>/        # drop documents here
  archive/<company>/      # copies of originals after ingest, named by sha256
  companies/<company>.duckdb
  shared/                 # dictionary.duckdb, audit.duckdb, analytics.duckdb
  review/<company>/       # unreadable files with .error.json reasons, pending mappings
  notes/<company>/        # notes to accounts and auditor reports as Markdown
  evidence/  reports/  logs/  backups/
```

Edit `config.toml` to change settings; `fin init` never overwrites it. Run `uv run fin doctor` after editing.
Never open the `.duckdb` files directly; use `fin`.

## Guardrails

Claude works for you without approval prompts. The safety net is in `fin`: every data change goes through it, backs
up the database first to `backups/<run_id>/` and is recorded in the audit log. Every action Claude takes is logged to
`~/FinAnalysis/logs/claude_actions.jsonl`. `fin delete` asks you to type the company name when you run it yourself.

## Update and uninstall

- Update: `uv run fin update`. It refuses if you have local changes in this folder, backs up all databases, pulls the
  latest code, installs dependencies, refreshes the metrics and runs `fin doctor`. If a step fails (E005), the
  backup is in `~/FinAnalysis/backups/<run_id>/`.
- There are no database migrations. If a new version changes the database layout, move `~/FinAnalysis` away and run
  `uv run fin init` again (the doctor says so with E002).
- Uninstall: delete the `fin-analyst` folder. To remove your data as well, delete `~/FinAnalysis` yourself.

To modify the app with Claude Code, see [docs/CUSTOMISING.md](docs/CUSTOMISING.md).

## Troubleshooting

When a command fails, note the error code and the run id it prints. Exit codes: 0 success (held documents and
findings included), 1 finished but some files failed, 2 the command could not run, 3 refused until you confirm. Every run is in
`~/FinAnalysis/logs/<date>/<run_id>.jsonl`; `uv run fin audit <company>` shows a company's history.

| Code | Meaning | What to do |
|---|---|---|
| E001 | A required program (uv, node, quarto), `config.toml` or the audit database is missing | Run the fix `fin doctor` shows (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database was made by an older fin | Run the fix shown (`brew upgrade`, correct `config.toml`, or move the data folder away and run `uv run fin init`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or fix the folder permissions |
| E005 | `fin update` stopped: local changes in this folder, or a git, uv or npm step failed | Commit or discard local changes, or fix the failing step, and run it again. The backup is in `backups/<run_id>/` |
| E101 | File is unreadable or corrupt | Replace it with a good copy and ingest again. The file is in `review/<company>/` |
| E102 | File is password protected | Save an unprotected copy and ingest that |
| E103 | Unsupported file format | Convert it to `.xlsx` or a text PDF |
| E104 | Scanned PDF (no text layer); fin cannot read it | Get an Excel or text-PDF version, or ask Claude to read the pages (see "Pages fin could not read") |
| E105 | The same file (same sha256) is already loaded | Nothing to do. Use `--force` to load it again as a new version |
| E201 | No tables found in the file | Check the file has statement tables; read the reason in `review/<company>/` |
| E202 | Fiscal periods or column headers not detected | Fix the headers in the file, then ingest again |
| E203 | Unit (crores, lakhs, ...) not detected | Add a unit line such as "Rs. in Crores" to the file, then ingest again |
| E204 | Parse error while reading the file | Read the reason in `review/<company>/<file>.error.json` and fix the file |
| E205 | A reading file (`--reading`) cannot be used: missing columns, unknown statement or scope, bad figure, date or source, or nothing fin did not read already | Fix the line named in the message and run again |
| E301 | A label is not in the dictionary | Loaded as unconfirmed. Run `/review` to confirm a mapping |
| E302 | A proposed mapping has low confidence | Same: check it in `/review` |
| E303 | A label maps to conflicting accounts | Same: you decide in `/review` |
| E304 | A mapping proposal or review request cannot be applied (unknown account, wrong statement, unknown id or label, invalid new account) | Nothing was applied. Check `fin review list` and the account name, then retry |
| E406 | `fin findings accept` named a finding that does not exist or is not open | Nothing was accepted. Check the ids with `uv run fin findings list <company>` |
| E501 | A database is in use by another `fin` command | Wait for it to finish and run again |
| E502 | `fin query` was given something other than one SELECT | Use `fin sql --write` for changes you want made |
| E503 | A database could not be created from its schema file | Report the error |
| E504 | A SQL statement failed (syntax, unknown column, constraint) | Nothing changed. Fix the statement |
| E601 | Report render failed | The last good report is kept. Run `uv run fin doctor` (Quarto) and try again |
| E602 | The narrative contains hand-typed numbers | Replace each listed line with a `{{metric:...}}` or `{{fact:...}}` reference, or reword it |
| E603 | Dashboard build failed | The last good build is kept. Run `uv run fin doctor` (node) and try again |
| E604 | An evidence source file is missing from the archive | The page is still produced and flags it. Restore the original into `archive/` |
| E701 | The action needs your confirmation | `fin delete` run by you exits 3 when the typed name does not match; Claude uses `--yes` when you asked |

E401-E405 are not errors but finding codes (see "Findings"): the document is held, not rejected, and the command
exits 0.
