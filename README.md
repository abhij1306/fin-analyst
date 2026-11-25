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

`uv run fin parser use <name>` does the same as the flag; the choice is saved in `config.toml` (`[pdf] parser`). LlamaParse needs `LLAMA_CLOUD_API_KEY` in your environment (read only from there, never stored or logged). Figures an add-on reads are marked as read by it.

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

For a quality-of-earnings style review of one company, fin keeps questions for the company's accountant and proposed EBITDA adjustments beside the reported figures, never changing them: a difference is the company's to explain, so it becomes a query, not a fix. Claude drafts; you decide; fin sends nothing. Checkpoints (gates) stop the build until you approve them. Start with `/diligence <company>`; the full reference is [docs/diligence.md](docs/diligence.md).

## Pages fin can't read

fin reads Excel files and digital PDFs. When it can't read a statement page (an unusual layout, a scanned PDF)
and a PDF add-on is chosen, the add-on reads that page automatically (a scanned PDF only with Docling or LlamaParse, up to 40 pages, such
as a results filing). Whatever is still not read, fin lists by page. If you ask, Claude reads those pages and fin
loads that reading through the same checks. Figures read by the add-on or Claude are marked as such; whether you
trust them is your call. Whenever you want, `/verify` has Claude read a document again and fin compare that reading
with what is stored.

## Golden evaluation

To see how well fin reads your own documents, follow [docs/EVAL.md](docs/EVAL.md) (written for your Claude agent): `fin eval init <company>` lists the figures to check, you check them against the documents, `fin eval run <company>` scores fin, and `fin eval bundle` writes a results file with no company, party or file names to send back.

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
  [docs/development.md](docs/development.md).

## Documentation

| File | What it is |
|---|---|
| [docs/product.md](docs/product.md) | What fin does and how it behaves: the spec |
| [docs/diligence.md](docs/diligence.md) | The diligence extension: plan, rules and command reference |
| [docs/development.md](docs/development.md) | Where code lives and how to add an account, metric, check, profile or report section |
| [docs/errors.md](docs/errors.md) | Error codes: what each means and what to do |
| [docs/decisions/](docs/decisions/) | Product-level decisions and how to change them |
| [docs/EVAL.md](docs/EVAL.md) | Golden evaluation runbook |
| `CLAUDE.md` | Rules for Claude Code in this repo |

When a command fails it prints an error code and a run id; look the code up in [docs/errors.md](docs/errors.md).
