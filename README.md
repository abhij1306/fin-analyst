# fin-analyst

A local tool for looking at the financial health of companies. You drop financial documents (Excel, PDF) into a
folder. `fin` loads them into a local database, answers questions with the source cell or page for every number, and
produces reports and dashboards, all inside [Claude Code](https://claude.com/claude-code).

**What it does**

- Loads Excel and text-based PDF documents, one database per company, and checks them (balance sheet balances,
  subtotals add up, cash flow ties to cash, figures agree across documents, units make sense).
- Computes ratios and growth in SQL and traces every number back to its file and cell or page.
- Writes health reports (HTML, Word, PDF) and an Evidence dashboard.
- Logs every action, so any number, report or change can be audited.

**What it does not do**

- It analyses only. It gives no scores, rankings or recommendations; decisions stay with you.
- It does not read scanned PDFs (no text layer) and does not guess: unknown labels and missing numbers are flagged,
  never filled in.
- Everything stays on your Mac. The Python code never calls an AI service; Claude Code does the judgement work.

## Install

You need a Mac (Apple Silicon or Intel), an internet connection for the first install, and Claude Code.

```sh
git clone https://github.com/abhij1306/fin-analyst.git
cd fin-analyst
./install.sh
```

The installer checks you are on macOS, asks before installing [Homebrew](https://brew.sh) if it is missing, installs
`uv`, `node` and `quarto`, sets up the Python environment, creates your data folder and runs `uv run fin doctor`.
You can run it again at any time; it only installs what is missing. Its log is `~/FinAnalysis/logs/install.log`.

To keep your data elsewhere, set `FIN_DATA_DIR` (for example `export FIN_DATA_DIR="$HOME/Documents/FinAnalysis"`)
before installing, and add that line to `~/.zshrc`.

Check the installation at any time with `uv run fin doctor`. Each line says `ok` or `FAIL`, with an error code and
the fix (see Troubleshooting).

## Daily workflow

Start Claude Code in the `fin-analyst` folder with `claude`. Then:

| Step | You do | What happens |
|---|---|---|
| 1 | Drop files in `~/FinAnalysis/inbox/<company>/` (lower-case company name, for example `kestrel`) | |
| 2 | `/ingest <company>` (or `/ingest-batch` for every folder under `inbox/`) | Loads the files, checks them, and proposes accounts for labels it does not know. A file that fails goes to `review/<company>/` with a reason. A file already loaded is skipped (E105). |
| 3 | `/review [company]` | Lists the proposed mappings. Tell Claude which to confirm or reject; Claude Code asks you to approve a confirm. |
| 4 | `/ask <company> <question>` | Answer, calculation, source table (file and sheet!cell or page), the SQL, an evidence page and flags (unconfirmed mappings, missing data). |
| 5 | `/report <company>` | Writes a narrative that describes the numbers (it never types them) and renders HTML, Word and PDF into `reports/<company>/<scope>/`. |
| 6 | `/dashboard` (or `uv run fin dashboard`) | Refreshes the dashboard data and starts the Evidence dashboard at http://localhost:3000 (company, compare and data quality pages). It reads a copy of the data, so you can ingest while it is open. `uv run fin dashboard --build` makes a static site instead. |
| 7 | `/audit <company> [metric fiscal-year or --fact id]` | Traces a figure to its source, or lists the company's history. |

Until you confirm a mapping, the facts it affects are marked unconfirmed and are left out of metrics. Answers say so.

The slash commands call `fin` for you. You can also run `uv run fin --help` and use the commands directly.

### Changing data

Ask Claude in plain words, for example "set the FY24 revenue of kestrel to ...". Claude uses
`uv run fin sql <company> --write --sql "..."`, which backs up the company database to `backups/<run_id>/`, runs the
statement in one transaction and records it in the audit log. Claude Code asks you to confirm first.
`fin query` is read-only and refuses anything but one SELECT.

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
  review/<company>/       # failed files with .error.json reasons, pending mappings
  notes/<company>/        # notes to accounts and auditor reports as Markdown
  evidence/  reports/  logs/  backups/
```

Edit `config.toml` to change settings; `fin init` never overwrites it. Run `uv run fin doctor` after editing.
Never open the `.duckdb` files directly; use `fin`.

## Guardrails

They exist to stop accidents, not to stop you. Nothing is blocked. Before Claude deletes or moves files in your data
folder, opens a database directly, or runs `fin delete` or `fin review confirm`, Claude Code asks you to confirm
(E701). Approve only if you asked for that change. Data changes go through `fin`, which backs up first and records
them. Every action Claude takes is logged to `~/FinAnalysis/logs/claude_actions.jsonl`.

## Update and uninstall

- Update: `uv run fin update`. It refuses if you have local changes in this folder, backs up all databases, pulls the
  latest code, installs dependencies, migrates the databases and runs `fin doctor`. If a step fails (E005), the
  backup is in `~/FinAnalysis/backups/<run_id>/`.
- `uv run fin migrate` brings every database to the current layout on its own (it backs up first).
- Uninstall: delete the `fin-analyst` folder. To remove your data as well, delete `~/FinAnalysis` yourself.

To modify the app with Claude Code, see [docs/CUSTOMISING.md](docs/CUSTOMISING.md).

## Troubleshooting

When a command fails, note the error code and the run id it prints. Exit codes: 0 success, 1 finished but some files
or checks failed, 2 the command could not run, 3 refused until you confirm. Every run is in
`~/FinAnalysis/logs/<date>/<run_id>.jsonl`; `uv run fin audit <company>` shows a company's history.

| Code | Meaning | What to do |
|---|---|---|
| E001 | A required program (uv, node, quarto), `config.toml` or the audit database is missing | Run the fix `fin doctor` shows (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database needs migrating | Run the fix shown (`brew upgrade`, correct `config.toml`, or `uv run fin migrate`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or fix the folder permissions |
| E004 | A database is newer than this version of fin | Update fin: `uv run fin update` |
| E005 | `fin update` stopped: local changes in this folder, or a git, uv or npm step failed | Commit or discard local changes, or fix the failing step, and run it again. The backup is in `backups/<run_id>/` |
| E101 | File is unreadable or corrupt | Replace it with a good copy and ingest again. The file is in `review/<company>/` |
| E102 | File is password protected | Save an unprotected copy and ingest that |
| E103 | Unsupported file format | Convert it to `.xlsx` or a text PDF |
| E104 | Scanned PDF (no text layer); not supported | Get an Excel or text-PDF version |
| E105 | The same file (same sha256) is already loaded | Nothing to do. Use `--force` to load it again as a new version |
| E201 | No tables found in the file | Check the file has statement tables; read the reason in `review/<company>/` |
| E202 | Fiscal periods or column headers not detected | Fix the headers in the file, then ingest again |
| E203 | Unit (crores, lakhs, ...) not detected | Add a unit line such as "Rs. in Crores" to the file, then ingest again |
| E204 | Parse error while reading the file | Read the reason in `review/<company>/<file>.error.json` and fix the file |
| E301 | A label is not in the dictionary | Loaded as unconfirmed. Run `/review` to confirm a mapping |
| E302 | A proposed mapping has low confidence | Same: check it in `/review` |
| E303 | A label maps to conflicting accounts | Same: you decide in `/review` |
| E304 | A mapping proposal or review request cannot be applied (unknown account, wrong statement, unknown id or label) | Nothing was applied. Check `fin review list` and the account name, then retry |
| E401 | Balance sheet does not balance | Nothing from the document was loaded. Compare expected and actual in `review/<company>/<file>.error.json` with the source |
| E402 | A subtotal does not equal the sum of its lines | Same |
| E403 | Cash flow closing cash differs from balance sheet cash | Same |
| E404 | The same figure differs between documents | Same; decide which document is right |
| E405 | Unit sanity check failed (a figure is off by exactly a unit factor, such as 100) | Check the unit stated in the file |
| E501 | A database is in use by another `fin` command | Wait for it to finish and run again |
| E502 | `fin query` was given something other than one SELECT | Use `fin sql --write` for changes you want made |
| E503 | A database migration failed | The database was restored from `backups/`; report the error |
| E504 | A SQL statement failed (syntax, unknown column, constraint) | Nothing changed. Fix the statement |
| E601 | Report render failed | The last good report is kept. Run `uv run fin doctor` (Quarto) and try again |
| E602 | The narrative contains hand-typed numbers | Replace each listed line with a `{{metric:...}}` or `{{fact:...}}` reference, or reword it |
| E603 | Dashboard build failed | The last good build is kept. Run `uv run fin doctor` (node) and try again |
| E604 | An evidence source file is missing from the archive | The page is still produced and flags it. Restore the original into `archive/` |
| E701 | The action needs your confirmation | Approve the Claude Code prompt if you asked for it. Run directly, `fin delete` exits 3 on a wrong name |
