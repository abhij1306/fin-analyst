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

**Optional: a PDF add-on.** fin reads digital PDFs with pdfplumber. Some statement pages it can't read (standalone
and consolidated side by side, unusual headers, scanned results). With an add-on, fin hands exactly those pages to
one engine you choose, which reads their tables:

```sh
./install.sh --parser docling      # or liteparse, llamaparse
./install.sh --parser none         # remove it
uv run fin parser show             # what is chosen (or /parser in Claude Code)
```

| Add-on | Where it runs | Download and disk | Speed and cost |
|---|---|---|---|
| none (default) | | the base tool only | pages pdfplumber skips are listed as not read |
| [Docling](https://github.com/docling-project/docling) | your Mac (Apple silicon only: PyTorch has no Intel Mac build) | about 650 MB of Python packages (PyTorch, Docling) plus about 730 MB of models in `~/.cache/docling/models` | about 15 seconds a page; read 99-100% of the figures on our benchmark of skipped pages |
| [LiteParse](https://github.com/run-llama/liteparse) | your Mac | about 12 MB plus about 50 MB of models (its `ocria-nano` package, under its own model licence) | fast; pages with a text layer are never OCR'd, and scanned PDFs are left for Claude to read |
| [LlamaParse](https://github.com/run-llama/llama-parse-py) | LlamaIndex's cloud: the skipped pages are uploaded | a small Python SDK | paid per page; most accurate on hard tables; needs `LLAMA_CLOUD_API_KEY` in your environment |

`uv run fin parser use <name>` does the same as the install flag. The choice is saved in `config.toml`
(`[pdf] parser`), so running `./install.sh` again or `uv run fin update` keeps it. For LlamaParse, set the key in
your shell (`export LLAMA_CLOUD_API_KEY=...` in `~/.zshrc`); fin reads it only from the environment, never stores or
logs it, and saves LlamaParse's answer in the data folder (`cache/llamaparse/`) so the same file is never sent
twice. Figures an add-on reads are marked as read by it; everything else works the same without one.

**LlamaParse and your data.** LlamaParse is a cloud service: when it is the chosen parser, the PDF pages fin cannot
read are uploaded to LlamaParse for parsing. Documents can contain personal or confidential data (bank statements
carry counterparty names, payroll carries employee data). Choose a local parser (Docling or LiteParse) for documents
that must not leave the machine, and check your own or your client's data-handling rules before choosing LlamaParse.

To keep your data somewhere else, set `FIN_DATA_DIR` before installing (for example
`export FIN_DATA_DIR="$HOME/Documents/FinAnalysis"`) and add that line to `~/.zshrc`.

`uv run fin doctor` checks the installation at any time: each line is `ok`, `warn` or `FAIL` with the fix.

## Slash commands

Plain requests work; these are shortcuts. Each one runs `fin` (see `uv run fin --help`).

| Command | What it does |
|---|---|
| `/ingest <company>` | Load the company's inbox files, check them, propose accounts for new lines. `/ingest-batch` does every company. |
| `/review [company]` | Show lines waiting for an account; tell Claude which to confirm or reject. |
| `/parser [docling\|liteparse\|llamaparse\|none]` | Show the PDF add-on, explain the choices, and switch when you name one. |
| `/status [company]` | What is loaded, which periods are missing, held documents, findings, items waiting for you. |
| `/statements <company> [period]` | Profit and loss, balance sheet and cash flow in standard order, or an Excel export with every period and all metrics. |
| `/ask <company> <question>` | An answer with the calculation, the source cells or pages, the SQL and an evidence page. |
| `/report <company> [--peers a,b]` | Health report: key metrics, one section per category, latest quarters, peers, findings, data coverage. |
| `/dashboard` | Dashboards at http://localhost:3000: Company, Compare and Data quality. |
| `/audit <company> [metric year]` | Trace a figure back to its source, or show the company's history. |
| `/verify <company> <file>` | Have Claude read a loaded or held document again and compare that reading with what is stored, whenever you want; each run is recorded. |
| `/diligence <company>` | A diligence engagement: queries for the company's accountant and adjustments to EBITDA, with evidence, and the gates you approve. |
| `/eval [company]` | Golden evaluation: values you checked in your documents, fin scored against them, and a results file with no names to send back ([docs/EVAL.md](docs/EVAL.md)). |

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

## Diligence

A diligence engagement on one company keeps two things beside the reported figures, never changing them: questions
for the company's accountant, and proposed adjustments to EBITDA. A difference is the company's to explain, so it
becomes a query, not a fix. Claude drafts; you decide. fin never sends anything. Plan:
[docs/DILIGENCE_PLAN.md](docs/DILIGENCE_PLAN.md).

- `fin engagement open <company> --periods FY24,FY25,FY26 [--threshold N] [--stub FY27-YTD-SEP]`: one open
  engagement per company, with the years in scope, the query threshold in rupees (default `query_threshold` in
  `config.toml`) and optionally a current-year stub: the year to date after the last year (here April to
  September of FY27), from unaudited management data. The stub is labelled "unaudited management accounts"
  wherever it shows and is never annualised.
  `fin engagement status <company>` counts queries and adjustments by status; `fin engagement close <company>`
  (needs gate G4, unless the engagement has no queries and no adjustments).
- Queries: `fin query raise <company> --finding <id> --question "..."` (the finding's period, difference and
  sources; the finding is not changed) or `--amount N --period P --source "file sheet!cell" --question "..."`.
  A query starts as a draft (`fin query edit` changes its wording), then `fin query approve`, `sent` (when you have
  sent it), `answer --text`, `close`; `withdraw --reason` any time before it is closed; `list [--status]`.
- Adjustments: `fin adjust propose <company> --period --scope --category --description` with evidence
  (`--query`, `--finding` or `--source`), the effects (`--revenue-effect` and `--cost-effect`, signed rupees; the
  EBITDA effect, `--amount`, is always revenue effect minus cost effect, and `--amount` alone is a cost effect of
  minus that) and optionally `--account`. A reclassification between two cost accounts names both
  (`--account` the cost moves to, `--from-account`, `--moved-amount`) and has no net effect. Then
  `fin adjust accept <ids>` or `reject <ids> --reason`; `list [--status]`. Categories are `adjustment_categories`
  in `config.toml`.
- Pass-through: `fin engagement passthrough <company> [--customer CUST-0001 ... --source "..."] [--clear]` records your
  list of pass-through customers (SI/reseller deals at about zero margin, as tokens from the sales register; E845 for a
  token not on a loaded sales register), replacing the earlier list; without options it shows the list and the
  revenue split. The views `revenue_split` (per period: sales revenue as core and pass-through, the pass-through
  lines with and without a Purchase Id, the linked OEM purchase cost, core revenue growth) and `passthrough_lines`
  (each pass-through line with its OEM purchase) read it. Growth is on core revenue only. Claude proposes the
  `normalisation` adjustment that strips pass-through revenue and its cost; you accept it. `fin` values nothing.
- `fin engagement bridge <company> [--period FY25] [--scope standalone] [--json]`: per period and scope of the
  latest engagement, the EBITDA bridge (reported EBITDA, each accepted adjustment, adjusted EBITDA; proposed ones are
  listed as not applied) and the EBITDA-to-cash walk down to CFO and free cash flow, with cash conversion. Money in
  the documents' unit, as `fin statements`; `--json` in rupees. A line whose input is missing shows `-` and names it.
  The same figures are the views `adjusted_ebitda` (also adjusted revenue and the margin on it), `ebitda_bridge` and
  `ebitda_to_cash`, queryable with `fin sql`.
- Gates: checkpoints you approve, in order. fin checks each one's checklist; Claude prepares a gate and shows you
  `fin gate status <company>` (each gate's state, its checklist lines and what it unlocks), and only you approve it
  (`fin gate approve <company> <gate>`). A Claude Code hook asks you before any command Claude runs that looks like
  a gate approval, and `fin sql --write` cannot write gate approvals, queries, adjustments or the engagement
  (E806): they change only through `fin engagement|query|adjust|gate` (and the source file tables only through
  `fin ingest --kind`). An approved gate reopens on its own when the
  data under it changes, and you approve it again.
  - **G1 Ingestion:** every year in scope has mapped figures from a loaded document, no held documents, no mappings
    waiting for review. Once the engagement has a trial balance (or a stub), also: a trial balance and Ledger
    Vouchers for every year and the stub, every trial balance ledger in its Ledger Vouchers, every ledger mapped to
    an account, and each year's `tb_vs_statements` run on the current data with every difference at or above the
    threshold in a query (the stub is checked against its Ledger Vouchers only). A changed figure (for example a
    fix with `fin sql --write`), a new source file, a mapping change or a new reconciliation reopens it, and so
    does any change after which its checklist fails. Once a payroll file is loaded, G1 also needs the user to
    have confirmed its unit (`fin payroll confirm-unit`).
  - **G1 and bank statements:** once a bank statement is loaded, also every bank parse ties (opening + credits −
    debits = closing, or the user has accepted its finding). Overlapping periods and balance gaps are findings for
    queries, listed in G1 but not blocking it.
  - **G2 Reconciliation:** no query in draft. A new or reworded query reopens it; sending, answering or closing an
    approved query does not. Needed by `fin adjust accept|reject`.
  - **G4 Adjustments:** no proposed adjustment, and every accepted one has evidence. Needed by
    `fin engagement close`.

  A command that needs a gate that is not approved refuses with E801; a gate that cannot be approved yet, E802.
  `fin status` shows an open engagement's queries, proposed adjustments and gates.
- Source files: `fin ingest <company> <file> --kind trial_balance|ledger_vouchers|registers|payroll [--profile <name>]` loads a Tally
  Trial Balance (Detailed mode) or Ledger Vouchers export into `trial_balance` or `ledger_entries` (and `ledger_blocks`, every ledger block the export has, even one with no voucher lines), or a billing-system
  registers workbook (a sales sheet and a purchase sheet) into `register_lines`, each row with
  its file (`source_files`: sha256, archive copy, profile) and sheet!row. The period comes from the export's header
  lines and must be a year of the open engagement, or exactly its stub (E808). A column profile (`templates/profiles/*.toml`) says how
  to read the layout; a layout no profile matches fails with E807 and lists the header lines found, so Claude can
  write a profile. A trial balance with group rows only is refused (E809: export it in Detailed mode); a group not
  equal to the sum of its ledgers is a finding, kept as filed. Load a period's trial balance before its Ledger
  Vouchers (E811); fin then lists the trial balance ledgers missing from the Ledger Vouchers. A later file of the
  same kind and period supersedes the earlier one. A registers workbook has no period header: its period comes from
  its invoice dates (a later registers file replaces the earlier whatever its period). Amount in Rs is before GST.
  The same invoice number twice (sales: the number; purchases: the vendor and number) is a finding, both rows kept
  (E813 only when a row is not an invoice line: no number, date, party or Amount in Rs, or a date in another format).
- Bank statements: `fin ingest <company> <file> --kind bank [--profile <name>]` loads an Excel or PDF statement (ICICI,
  SBI or Axis; INR or USD accounts, the profiles `templates/profiles/bank_*.toml`) into `bank_statements` and
  `bank_lines`, each row with its file and sheet!row (Excel) or page and row (PDF). Remarks keep their text with
  known masked names replaced by tokens. A statement covers part of one period of the engagement (it must not cross
  a year end, E808). A PDF is read by the PDF add-on you chose (`fin parser use`; E816 when there is none): its
  tables are saved in the data folder as `cache/bank/<sha256>.json`, so the same file is never parsed twice and
  every row traces to that parse. LlamaParse uploads the pages to its cloud and needs `LLAMA_CLOUD_API_KEY` in
  the environment (a missing key or a failed call is E206; nothing loaded); use Docling or LiteParse for statements
  that must not leave your Mac. A statement whose opening + credits − debits is not its closing balance is a
  finding (`bank_tie`), as are statements of one account that overlap (`bank_overlap`) or whose balances do not
  join (`bank_balance_gap`: a statement or transaction may be missing). Findings, not failures: the rows stay as
  filed and you decide (`fin findings accept`, or a query to the accountant).
- Names: party ledgers (under Sundry Debtors and Sundry Creditors) are stored and printed as tokens (`CUST-0001`,
  `VEND-0001`), the same name the same token in every file; register customers, vendors, handlers and submitted-by
  are tokens too (`CUST-`, `VEND-`, `USER-`); known names in Ledger Vouchers particulars are
  replaced too. A Ledger Vouchers ledger not in its trial balance is masked as `LEDG-0001`, since fin cannot tell
  whether it is a party. The real names stay on your Mac in `mask_values`, which `fin sql` refuses to read (E810).
- Payroll: `--kind payroll` loads the Sparsh snapshot (one row per employee, profile `sparsh_payroll`) into
  `payroll_lines`; the snapshot date in its header ("Employee Master as on 31-Mar-2025") must fall in a year of the
  engagement or its stub (E808). Employee name and code are tokens (`EMP-0001`, `ECODE-0001`). Gross salary and CTC are
  read as annual rupees: fin warns when their medians are under Rs 1,50,000 (they look monthly), and you tell it
  the unit with `fin payroll confirm-unit <company> <file> --annual|--monthly`. G1 shows "unit confirmed annual by
  the user" for each payroll file and fails until you confirm; `--monthly` keeps it failing (fin does not convert).
  A row with no employee name or code, or a bad date, is E821 and nothing loads.
- Ledger mapping: each trial balance ledger maps to a Schedule III account. `fin map ledgers <company>` lists the
  ledgers waiting; Claude proposes accounts with `fin map ledgers <company> <file.json>`; you confirm or reject
  them with `fin review list|confirm|reject <company> ... --ledgers` (`--account` to choose another). fin never
  confirms one itself.
- `fin reconcile <company> [name] [--period FY25] [--json]` (name: `tb_vs_statements`, `sales_register_vs_gl`,
  `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`; none: every one whose inputs are loaded). The last four compare
  the registers, Ledger Vouchers, trial balance and payroll; fin drafts no query for them: Claude groups the lines
  at or above the threshold into issues with `fin query raise <company> --items 12,13 --question "..."` and you approve
  them; foreign-currency rate differences are `fx` lines, listed, never queried (E840-E842). `tb_vs_statements`: each year's trial balance, rolled up by
  confirmed ledger mapping, against the audited standalone statement figures, one line per account (the year's
  profit from the income and expense ledgers rolls into other equity, as the balance sheet shows it). A line whose
  difference is at or above the threshold becomes a draft query (origin `recon`; you approve and send it); a line
  below it is listed. Ledgers not mapped to an account are flagged and no query is drafted until every ledger is
  mapped. Nothing is changed to make it tie. The stub has no audited statement: its figures are the view
  `stub_figures`, labelled unaudited (E812 if asked to reconcile it).

Every change, and every gate approval, is recorded in the audit table `diligence_events`.

Planned (docs/DILIGENCE_PLAN.md §7): sales and purchase registers, bank statements and payroll; more
reconciliations; service-period recast; the query export; customer analytics.

## Pages fin can't read

fin reads Excel files and digital PDFs. When it can't read a statement page (an unusual layout, a scanned PDF)
and a PDF add-on is chosen, the add-on reads that page automatically (a scanned PDF only with Docling or LlamaParse, up to 40 pages, such
as a results filing). Whatever is still not read, fin lists by page. If you ask, Claude reads those pages and fin
loads that reading through the same checks. Figures read by the add-on or Claude are marked as such; whether you
trust them is your call. Whenever you want, `/verify` has Claude read a document again and fin compare that reading
with what is stored.

## Golden evaluation

To see how well fin reads your own documents, and to help improve it, follow [docs/EVAL.md](docs/EVAL.md) (written
for your Claude agent). `uv run fin eval init <company>` lists the figures to check with where each is printed; you
check the values against the documents; `uv run fin eval run <company>` scores fin against them (pass, fail, missing
in fin; lines you have not checked are not scored); `uv run fin eval bundle` writes a results file to send back. The
bundle has no company, party or file names and, unless you add `--with-figures`, no figures.

## Your data

Everything lives in `~/FinAnalysis` (or `FIN_DATA_DIR`), outside this folder; updating or uninstalling never touches
it.

```
~/FinAnalysis/
  config.toml           # display unit, default scope, fiscal year start, check tolerances, PDF add-on,
                        # diligence query threshold and adjustment categories
  inbox/<company>/      # drop documents here
  companies/            # one database per company
  shared/               # chart of accounts, audit log, dashboard data
  reports/<company>/    # health reports and Excel exports
  review/<company>/     # files fin could not load, with the reason
  evals/                # golden sets, eval reports and results bundles (docs/EVAL.md)
  archive/  evidence/  notes/  logs/  backups/
  cache/llamaparse/     # LlamaParse's saved answers, when that add-on is used
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
A command-line usage error (a wrong option, argument or value, exit 2) is reported by the parser before a run starts, so it has no error code, run id or log.

| Code | Meaning | What to do |
|---|---|---|
| E001 | A required program (uv, node, quarto), `config.toml` or the audit database is missing, or a program cannot be started | Run the fix `fin doctor` shows (`./install.sh` or `uv run fin init`) |
| E002 | A program is the wrong version, `config.toml` is invalid, or a database was made by an older fin | Run the fix shown (`brew upgrade`, correct `config.toml`, or move the data folder away and run `uv run fin init`) |
| E003 | The data folder is missing or not writable | Run `uv run fin init`, or fix the folder permissions |
| E005 | `fin update` stopped: local changes in this folder, or a git, uv or npm step failed; or the `uv sync` of `fin parser use` failed | Commit or discard local changes, or fix the failing step, and run it again. The backup is in `backups/<run_id>/` |
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
| E206 | The PDF add-on (Docling, LiteParse or LlamaParse; the message names it) failed on the pages pdfplumber skipped, could not download its models, or (LlamaParse) found no `LLAMA_CLOUD_API_KEY`; a warning, the pages stay unread | Read the warning in the run log; `uv run fin doctor`; set the key; or ask Claude to read the pages |
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
| E604 | An evidence source file is missing from the archive, cannot be opened, or has no page a figure names | The page is still produced and flags it. Restore the original into `archive/` |
| E701 | The action needs your confirmation | `fin delete` run by hand exits 3 when the typed name does not match; Claude uses `--yes` when you asked |
| E801 | A diligence gate this command needs is not approved: never approved (open), or its data changed since it was approved (reopened) | Nothing changed. See what the gate needs with `uv run fin gate status <company>`; once its checklist passes, you approve it with `uv run fin gate approve <company> <gate>` |
| E802 | A gate cannot be approved: its checklist fails, or an earlier gate is not approved | Nothing approved. Fix the failing checklist lines shown (or approve the earlier gate first), then approve again |
| E803 | No open engagement for the company, or one is already open | Open one with `uv run fin engagement open <company> --periods ...`, or close the open one first |
| E804 | An adjustment cannot be proposed: no evidence (query, finding or source), a category not in `config.toml`, a period not in the engagement, an unknown account, effects that do not add up (EBITDA effect not revenue effect minus cost effect), or a reclassification that is not valid | Nothing changed. Add the evidence or correct the value and run again |
| E805 | An unknown query, adjustment or finding id, or a status change that is not allowed | Nothing changed. Check the ids and statuses with `uv run fin query list` or `uv run fin adjust list` |
| E806 | `fin sql --write` was given a statement that writes to a diligence table (`engagement`, `accountant_queries`, `adjustments`, `gate_approvals`, `source_files`, `trial_balance`, `ledger_entries`, `ledger_blocks`, `register_lines`, `bank_statements`, `bank_lines`, `payroll_lines`, `payroll_units`, `ledger_accounts`, `recon_runs`, `recon_items`, `passthrough_customers`, `query_items`) | Nothing was run. Make the change with `uv run fin engagement`, `fin query`, `fin adjust` or `fin gate`, load the source file again with `fin ingest --kind`, map ledgers with `fin map ledgers` and `fin review --ledgers`, or run `fin reconcile` again |
| E807 | `fin ingest --kind`: the column profile named is unknown or invalid, or no profile matches the file's headers | Nothing loaded. The message lists the header lines found: write a profile in `templates/profiles/` (docs/CUSTOMISING.md) and ingest with `--profile` |
| E808 | `fin ingest --kind`: the export's header gives no period, or a period that is not a year of the open engagement nor exactly its stub; or `fin engagement open --stub` names a stub that is not the year to date of the fiscal year after the last period | Nothing loaded or opened. Check the export's period, or the engagement's periods and stub (`uv run fin engagement status <company>`) |
| E809 | A trial balance with group rows only (Tally's Condensed export) | Nothing loaded. Export the trial balance again in Detailed mode, with ledgers, and ingest that |
| E810 | `fin sql` was given SQL naming `mask_values` (the real names behind the tokens), or calling `query`, `query_table` or `read_duckdb`, which could reach it | Nothing was run. Work with the tokens; fin never prints the real names |
| E811 | Ledger Vouchers ingested before their period's trial balance | Nothing loaded. Ingest the period's trial balance first (it says which ledgers are parties), then the Ledger Vouchers |
| E812 | `fin reconcile`: the period is not a year of the engagement (the stub has no audited statement), or it has no loaded trial balance or no audited standalone statement figures | Nothing recorded. Load the period's trial balance and statements, or see the stub's figures in the view `stub_figures` |
| E813 | `fin ingest --kind registers`: a register row is not an invoice line: no invoice number, date, party or Amount in Rs, a date not in the profile's date format, or a row the profile does not skip (a total) | Nothing loaded. The message names the file, sheet!row and what is missing: fix the cell, or add the row's name to `[rows] skip` in the profile |
| E816 | A PDF bank statement cannot be read: `[pdf] parser` is `none`, its add-on is not installed, the parser could not read a page, or it found no table | Nothing loaded. Choose a PDF add-on (`uv run fin parser use docling\|liteparse\|llamaparse`; a local one keeps the pages on this Mac) and ingest again |
| E817 | A bank statement's header lines give no account number, or no opening or closing balance when its first or last row has no balance to stand in for it | Nothing loaded. Check the header lines against the profile's `[header]` patterns in `templates/profiles/bank_*.toml` (docs/CUSTOMISING.md) |
| E818 | The saved parse of a bank PDF (`cache/bank/<sha256>.json` in the data folder) cannot be read | Nothing loaded. Delete the file to parse the PDF again |
| E840 | `fin reconcile`: a reconciliation (`sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`) cannot run: a period not in the engagement, Ledger Vouchers, registers covering the period, a trial balance or a payroll file (unit confirmed annual) not loaded, or no ledger confirmed to the account the books side reads (revenue, purchases, employee expense) | Nothing recorded. Load or map what the message names, then run it again |
| E841 | `fin query raise --items` names a reconciliation line that does not exist, is not of the open engagement, or is not in the latest run (the data was reconciled again) | Nothing raised. Take the line ids from the latest `fin reconcile` output |
| E842 | `fin query raise --items` names a line that cannot be queried: matched, fx, not reconciled, unmapped, a month or party roll-up, below the threshold, already in a query, or lines of different periods | Nothing raised. Raise lines at or above the threshold, one period at a time |
| E820 | `fin payroll confirm-unit` names no loaded payroll file of the open engagement (unknown name or id, not a payroll file, or superseded by a later snapshot, or several loaded files with that name) | Nothing recorded. The message lists the loaded payroll files: use one's file name or file_id |
| E821 | A payroll snapshot row has no employee name or code, or a joining or resign date that is not a date in the profile's format | Nothing loaded. Ask the company for a corrected file, or fix the profile's `date_format`, and ingest again |
| E830 | `fin eval run`: the company has no golden set | Make one with `uv run fin eval init <company>` ([docs/EVAL.md](docs/EVAL.md)) |
| E831 | The golden set cannot be read: a missing column, an unknown kind, scope, account or unit, a figure that is not a number, a checked line with no value or source, or the same check twice | Nothing scored or changed. Fix the lines the message lists in `evals/<company>/golden.csv` |
| E832 | `fin eval bundle`: no company database to evaluate, or none for the company named | Nothing written. Load the company's documents first |
| E833 | `fin eval bundle`: the results would contain a real name (a masked party, a company or a file name) | Nothing written; the name is not printed. Report the run id: a bundle must never carry names |
| E845 | `fin engagement passthrough`: a customer token that is not on a loaded sales register, or no sales register is loaded | Nothing changed. Load the registers (`fin ingest <company> <file> --kind registers`) and use a token from `SELECT DISTINCT customer FROM register_lines WHERE kind = 'sales'` |

The code that raises each error is in `src/fin/audit/errors.py`.
