# fin-analyst — rules for Claude Code

Local financial health analysis tool. Ingests company financial documents into DuckDB, answers questions with evidence, generates reports and dashboards. Spec: `docs/product.md`.

## Purpose and focus

- fin stores and analyses the financial data that established private companies send for their own business
  (mostly Excel, some PDF). Their documents are well formatted: extraction is not a problem to solve.
- The focus areas are mapping, analysis (metrics), diligence, and reports and dashboards. Put the work there.
- Keep extraction simple. Do not add parsing heuristics, layout special cases or new extraction code for unusual
  formats. The PDF add-on the user chose (`[pdf] parser`: Docling, LiteParse or LlamaParse) runs first on the pages
  pdfplumber skips; its adapter only turns the engine's output into fin's table grids. When neither can read
  something, Claude reads it (`fin ingest --reading`), and that is the fallback.
- Claude does not verify its own reading. Whether a Claude-read figure is trusted is the user's decision.

## Using the tool

- Use `fin` (`uv run fin ...`) for all data access. Change data only when the user asks, and through `fin` (`fin sql --write`, `fin review confirm`, `fin delete`), which backs up and logs.
- Every number you state must come from `fin` output. Never do arithmetic in prose; ratios and growth are computed in SQL.
- Answers to questions use this format: answer, calculation, source table (file + sheet!cell or page), SQL, flags.
- Flag unconfirmed mappings, findings (open and accepted), and missing data. Never fill gaps with assumptions.
- What a company filed that does not add up is a finding, not a failure. Show it neutrally and ask the user: save
  as is (`fin findings accept`) or have Claude fix it.
- Mappings: you propose (`fin map propose`); the user reviews them.
- Report narrative: describe, don't judge. No recommendations, ratings, or buy/don't-buy language. Numbers in narrative only as inline references, never typed.
  Exception, the FDD report (`fin report --fdd`, `docs/fdd.md` F8): findings may carry severity, implications and deal
  considerations; still no valuation, investment rating or buy/don't-buy language. Claude proposes findings; only the
  user marks one final.
- If a command fails, report the error code and run_id. Don't work around a failure.

## Diligence

Rules for diligence work (`fin engagement`, `fin query`, `fin adjust`, `fin gate`; `/diligence`;
`docs/diligence.md`):

- Differences in diligence data are queried, never fixed. A difference is the company's: raise a query
  (`fin query raise`) or propose an adjustment with evidence (`fin adjust propose`); never change the data to make it
  tie. Fixing fin's own reading errors (a wrong mapping, a misread figure) is still fine.
- Queries: you draft them; the user approves them. fin never sends anything: run `fin query sent` only when the user
  says they sent it. Approve, close, withdraw, and accept or reject adjustments only when the user says so.
- Gates stop the build: a command that needs a gate that is not approved refuses (E801), and a gate reopens on its
  own when the data under it changes. You prepare a gate: run `fin gate status <co>`, fix what its checklist says
  through the usual commands, and show the user the result. Only the user approves a gate: never run
  `fin gate approve` unless the user says so; a hook asks the user before any command that looks like a gate
  approval. The diligence tables change only through `fin engagement|query|adjust|gate`: `fin sql --write` refuses
  to write them (E806).

## Customising

This app is meant to be modified with Claude Code. Keep modules small and readable, and update `docs/product.md` with every change that changes behaviour (`CHANGELOG.md` is written at release). How to add an account, metric, validation gate or report section, how the schema and tests work, and the module map: `docs/development.md`.

## Coding rules

- Python 3.12 via uv. Add dependencies only with `uv add`, and say why. Commit lockfiles. macOS is the only target.
- Every failure path raises an error class with a code (table: `docs/errors.md`). No bare `except`, no silent defaults.
- Every `fin` command creates a run record and logs through `src/fin/audit/logger.py`. No `print` for logging.
- Database access only through `src/fin/db/conn.py`: short-lived connections, read-only unless the command writes.
- Each document loads in one transaction: stage → validate → commit or roll back.
- Only synthetic fixtures in the repo; never commit anything from the data folder.
- Prefer the simplest working design. No speculative features.

## Quick reference

- Data folder: `~/FinAnalysis` (override `FIN_DATA_DIR`)
- Health check: `uv run fin doctor`
- What is loaded and waiting per company: `uv run fin status`; findings: `uv run fin findings list`
- Tests: `uv run pytest`
- Golden evaluation on real documents (golden sets in the data folder, never committed): `uv run fin eval init|run|bundle`, runbook `docs/client-handoff.md`
- Lint: `uv run ruff check .`
