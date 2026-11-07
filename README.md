# fin-analyst

Turn company financial statements into a database you can question, chart and report on, from inside
[Claude Code](https://claude.com/claude-code).

You drop annual reports and quarterly results (Excel or PDF) into a folder and talk to Claude. `fin`, the tool
underneath, loads every figure into a local database with its source cell or page, maps it to a standard chart of
accounts (Indian Schedule III formats), checks that the numbers add up, and computes about 65 metrics. You get
standard statements, answers with sources, health reports and dashboards.

## How you use it

Start Claude Code in this folder (`claude`) and ask in plain words. For example:

> Load the files I put in the inbox for kestrel.
>
> What was kestrel's EBITDA margin over the last three years, and why did it change?
>
> Show bluepeak's balance sheet for FY25.
>
> Compare kestrel and bluepeak on return on capital employed.
>
> Write a health report for kestrel with bluepeak as a peer.
>
> Open the dashboard.

Claude runs `fin` for you, shows where every number came from, and asks you whenever a decision is yours.

## What happens to your documents

1. **Load.** Files in `~/FinAnalysis/inbox/<company>/` are read: every figure keeps its file, sheet and cell (or
   page), its unit (lakh, crore, ...) and its period (`FY25`, a quarter like `FY26-Q1`, a half `FY26-H1`, nine
   months `FY26-9M`), standalone or consolidated.
2. **Map.** Each line ("Revenue from operations", "Trade payables", ...) is matched to an account in the chart of
   accounts. A line fin has not seen before waits for review: Claude proposes an account (or a new one), you
   confirm. Confirmed mappings are remembered for next time.
3. **Check.** Does the balance sheet balance? Do subtotals add up? Does closing cash match? Does this document agree
   with earlier ones for the same period? What doesn't add up becomes a **finding**, never a silent fix (see
   [Findings](#findings)).
4. **Analyse.** Standard statements in the filing's own unit, and metrics by category: profitability, returns,
   liquidity, solvency, working capital, cash flow, growth (with 3- and 5-year CAGR), earnings quality, per share,
   lending (NBFCs and banks) and trailing twelve months. Each metric has a definition, a formula and the lines it
   used. A metric with missing inputs is left blank, never guessed.
5. **Report.** Health reports (HTML, Word, PDF) and dashboards (company, compare, data quality).

fin analyses; it does not judge. No scores, ratings or recommendations: decisions stay with you. Everything stays on
your Mac.

## Install

You need a Mac (Apple Silicon or Intel), an internet connection for the first install, and Claude Code.

```sh
git clone https://github.com/abhij1306/fin-analyst.git
cd fin-analyst
./install.sh
```

The installer asks before installing [Homebrew](https://brew.sh) if it is missing, then installs `uv`, `node`,
`quarto` and `poppler`, sets up the tool, creates your data folder (`~/FinAnalysis`) and runs `uv run fin doctor`.
Running it again only installs what is missing. Its log is `~/FinAnalysis/logs/install.log`.

**Optional: the Docling PDF add-on.** fin reads digital PDFs with pdfplumber. Some statement pages it can't read
(standalone and consolidated side by side, unusual headers, scanned results). With the add-on, fin hands exactly those
pages to [Docling](https://github.com/docling-project/docling), which reads their tables (with OCR) on your Mac:

```sh
./install.sh --docling      # add it (Apple Silicon only)
./install.sh --no-docling   # remove it
```

| Install | Download and disk | First run |
|---|---|---|
| `./install.sh` (default) | the base tool only | nothing more to download |
| `./install.sh --docling` | about 650 MB more of Python packages (PyTorch, Docling) plus about 730 MB of Docling models, downloaded by the installer into `~/.cache/docling/models` | the first PDF with skipped pages loads the models (some seconds); each page takes about 15 seconds |

The choice is saved in `config.toml` (`[pdf] docling = true`), so running `./install.sh` again or `uv run fin
update` keeps it. Figures Docling reads are marked as such and count as unverified until a reading agrees
(`/verify`). Intel Macs can't use it (PyTorch has no Intel Mac build); everything else works the same without it.

To keep your data somewhere else, set `FIN_DATA_DIR` before installing (for example
`export FIN_DATA_DIR="$HOME/Documents/FinAnalysis"`) and add that line to `~/.zshrc`.

`uv run fin doctor` checks the installation at any time: each line is `ok`, `warn` or `FAIL` with the fix.

## Slash commands

Plain requests work; these are shortcuts. Each one runs `fin` (see `uv run fin --help`).

| Command | What it does |
|---|---|
| `/ingest <company>` | Load the company's inbox files, check them, propose accounts for new lines. `/ingest-batch` does every company. |
| `/review [company]` | Show lines waiting for an account; tell Claude which to confirm or reject. |
| `/status [company]` | What is loaded, which periods are missing, held documents, findings, items waiting for you. |
| `/statements <company> [period]` | Profit and loss, balance sheet and cash flow in standard order, or an Excel export with every period and all metrics. |
| `/ask <company> <question>` | An answer with the calculation, the source cells or pages, the SQL and an evidence page. |
| `/report <company> [--peers a,b]` | Health report: key metrics, one section per category, latest quarters, peers, findings, data coverage. |
| `/dashboard` | Dashboards at http://localhost:3000: Company, Compare and Data quality. |
| `/audit <company> [metric year]` | Trace a figure back to its source, or show the company's history. |
| `/verify <company> <file>` | Have Claude read pages independently and compare with what is loaded. |

## Findings

When a filing doesn't add up, fin shows you; it doesn't reject the document or quietly change it. The checks:
balance sheet balances (E401), subtotals equal their lines (E402), closing cash ties to the balance sheet (E403), the
same figure agrees across documents, such as a restatement or a quarter against its year (E404), and no figure is off
by a unit factor (E405).

A document with open findings is **held**: stored, but kept out of answers, metrics and reports until you choose.
Claude shows each finding with both figures and where each came from, then asks you to:

- **Save as is.** The document loads exactly as filed; the findings stay attached and are mentioned in answers and
  reports.
- **Have Claude fix it**, for example a line mapped to the wrong account or a figure read wrongly. Fixes are backed
  up and logged, and held documents are checked again after each one.

## Pages fin can't read

fin reads Excel files and digital PDFs. When it can't read a statement page (an unusual layout, a scanned PDF)
and the Docling add-on is installed, Docling reads that page automatically (a scanned PDF only up to 40 pages, such
as a results filing). Whatever is still not read, fin lists by page. If you ask, Claude reads those pages and fin
loads that reading through the same checks. Figures read by Docling or Claude are marked as such until a second,
independent reading agrees (`/verify`).

## Your data

Everything lives in `~/FinAnalysis` (or `FIN_DATA_DIR`), outside this folder; updating or uninstalling never touches
it.

```
~/FinAnalysis/
  config.toml           # display unit, default scope, fiscal year start, check tolerances, Docling add-on
  inbox/<company>/      # drop documents here
  companies/            # one database per company
  shared/               # chart of accounts, audit log, dashboard data
  reports/<company>/    # health reports and Excel exports
  review/<company>/     # files fin could not load, with the reason
  archive/  evidence/  notes/  logs/  backups/
```

Change settings in `config.toml`, then run `uv run fin doctor`. Don't open the database files directly; ask Claude.

**Safety net.** Claude works without approval prompts. Every change to data goes through `fin`, which backs up the
database first (`backups/<run_id>/`) and records the change in the audit log; every action Claude takes is logged to
`logs/claude_actions.jsonl`. Deleting a company copies everything to `backups/` first.

## Update, uninstall, customise

- **Update:** `uv run fin update` backs up all databases, pulls the latest version and runs `fin doctor`.
- **New database layout:** there are no migrations. If a new version changes the layout (`fin doctor` says E002),
  move `~/FinAnalysis` aside, run `uv run fin init` and load your documents again.
- **Uninstall:** delete this folder. Your data stays in `~/FinAnalysis` until you delete it.
- **Customise:** add an account, a metric, a check or a report section with Claude Code; see
  [docs/CUSTOMISING.md](docs/CUSTOMISING.md).

## Error codes

When a command fails it prints an error code and a run id; Claude reports both. Exit codes: 0 success (held
documents and findings included), 1 finished but some files failed, 2 the command could not run, 3 refused until you
confirm. Every run is logged in `~/FinAnalysis/logs/<date>/<run_id>.jsonl`.

| Code | Meaning | What to do |
|---|---|---|
| E001 | A required program (uv, node, quarto), `config.toml` or the audit database is missing | Run the fix `fin doctor` shows (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database was made by an older fin | Run the fix shown (`brew upgrade`, correct `config.toml`, or move the data folder away and run `uv run fin init`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or fix the folder permissions |
| E005 | `fin update` stopped: local changes in this folder, or a git, uv or npm step failed | Commit or discard local changes, or fix the failing step, and run it again. The backup is in `backups/<run_id>/` |
| E101 | File is unreadable or corrupt | Replace it with a good copy and ingest again. The file is in `review/<company>/` |
| E102 | File is password protected | Save an unprotected copy and ingest that |
| E103 | Unsupported file format | Convert it to `.xlsx` or a text PDF |
| E104 | Scanned PDF (no text layer); fin cannot read it | Get an Excel or text-PDF version, or ask Claude to read the pages |
| E105 | The same file is already loaded | Nothing to do. Use `--force` to load it again as a new version |
| E201 | No tables found in the file | Check the file has statement tables; read the reason in `review/<company>/` |
| E202 | Fiscal periods or column headers not detected | Fix the headers in the file, or ask Claude to read the pages |
| E203 | Unit (crores, lakhs, ...) not detected | Add a unit line such as "Rs. in Crores" to the file, then ingest again |
| E204 | Parse error while reading the file | Read the reason in `review/<company>/<file>.error.json` and fix the file |
| E205 | A Claude reading file cannot be used (missing columns, unknown statement or scope, bad figure, date or source) | Fix the line named in the message and run again |
| E206 | Docling (the optional PDF add-on) failed on the pages pdfplumber skipped, or could not download its models; a warning, the pages stay unread | Read the warning in the run log; `uv run fin doctor`; or ask Claude to read the pages |
| E301 | A line is not in the chart of accounts | Loaded as unconfirmed. Run `/review` to confirm a mapping |
| E302 (warning) | A proposed mapping has low confidence; a warning, not an error | Check it in `/review` |
| E303 | A line maps to conflicting accounts | You decide in `/review` |
| E304 | A mapping proposal or review request cannot be applied (unknown account, wrong statement, unknown id, invalid new account) | Nothing was applied. Check `/review` and the account name, then retry |
| E401-E405 | Findings, not errors: balance sheet (E401), subtotal (E402), cash (E403), documents disagree (E404), unit factor (E405) | The document is held, not rejected. See [Findings](#findings) |
| E406 | A finding to accept does not exist or is not open | Nothing was accepted. Check the ids with `uv run fin findings list <company>` |
| E501 | A database is in use by another `fin` command | Wait for it to finish and run again |
| E502 | `fin sql` without `--write` was given something other than one SELECT | Use `--write` only for a change you asked for |
| E503 | A database could not be created from its schema file | Report the error |
| E504 | A SQL statement failed (syntax, unknown column, constraint) | Nothing changed. Fix the statement |
| E601 | Report render failed | The last good report is kept. Run `uv run fin doctor` (Quarto) and try again |
| E602 | The report narrative contains hand-typed numbers | Replace each listed number with a `{{metric:...}}` or `{{fact:...}}` reference, or reword it |
| E603 | Dashboard build failed | The last good build is kept. Run `uv run fin doctor` (node) and try again |
| E604 | An evidence source file is missing from the archive | The page is still produced and flags it. Restore the original into `archive/` |
| E701 | The action needs your confirmation | `fin delete` run by hand exits 3 when the typed name does not match; Claude uses `--yes` when you asked |

The code that raises each error is in `src/fin/audit/errors.py`.
