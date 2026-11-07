# Changelog

All notable changes to this project. Versions follow semantic versioning. The detailed history of how 1.0.0 was
built is in the git log and the merged pull requests.

## [1.0.0] - 2026-11-07

First version. A local tool, driven from Claude Code, that turns Indian company financial statements into a
database you can analyse, report on and chart.

### What it does

- **Ingest** Excel workbooks and digital PDFs (annual reports and quarterly / half-year / nine-month results,
  standalone and consolidated) with `fin ingest` and `fin ingest-batch`. Every figure keeps its source cell or page,
  unit and period (`FY25`, `FY26-Q1`, `FY26-H1`, `FY26-9M`). Each document loads in one transaction.
- **Claude reads what fin cannot.** Pages fin can't read (scanned, unusual layouts) are named in the ingest output;
  Claude Code reads them on request into a reading file (`fin ingest --reading`), and `fin verify` compares a loaded
  document with an independent Claude reading.
- **Chart of accounts** for the Schedule III formats (companies, NBFCs, banks): about 180 accounts and 690 label
  mappings in `templates/dictionary/`. Unknown labels go to review; Claude proposes an existing or a new account and
  the user confirms (`fin review`, `fin map`). Confirmed new accounts join the dictionary.
- **Findings, not failures.** Balance sheets that don't tie, subtotals that don't add up, cash that doesn't match,
  documents that disagree, restatements and unit mistakes are recorded as findings with both figures and their
  sources. A document with open findings is held until the user accepts it as filed (`fin findings accept`) or
  Claude fixes it (`fin sql --write`).
- **Standard statements** in the documents' own unit (`fin statements`, `fin export` to Excel).
- **About 65 metrics** in profitability, returns, liquidity, solvency, efficiency, cash flow, growth (with 3- and
  5-year CAGR), earnings quality, per share, lending and trailing twelve months, each with a definition, formula and
  the input lines behind it. EBITDA is taken before exceptional items.
- **Questions** answered with one read-only command, `fin sql`, with sources and an optional evidence page.
- **Health report** (`fin report`, HTML, Word and PDF) by metric category, with findings, data coverage, latest
  quarters and an optional peer comparison. Narrative describes, never judges; numbers come only from the data.
- **Dashboards** (`fin dashboard`): company, compare and data quality pages.
- **Safe changes.** Every command is logged with a run id; data changes back up first; `fin audit` traces a figure
  to its source; `fin delete`, `fin update`, `fin doctor` and `fin status` cover upkeep.
- One schema file per database; `fin init` creates the data folder (`~/FinAnalysis`).
