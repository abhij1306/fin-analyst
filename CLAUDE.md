# fin-analyst — rules for Claude Code

Local financial health analysis tool. Ingests company financial documents into DuckDB, answers questions with evidence, generates reports and dashboards. Full spec: `PLAN.md`.

## Using the tool

- Use `fin` (`uv run fin ...`) for all data access. Change data only when the user asks, and through `fin` (`fin sql --write`, `fin review`, `fin delete`), which backs up and logs.
- Every number you state must come from `fin` output. Never do arithmetic in prose; ratios and growth are computed in SQL.
- Answers to questions use this format: answer, calculation, source table (file + sheet!cell or page), SQL, flags.
- Flag unconfirmed mappings, findings (open and accepted), and missing data. Never fill gaps with assumptions.
- What a company filed that does not add up is a finding, not a failure. Show it neutrally and ask the user: save
  as is (`fin findings accept`) or have Claude fix it.
- Mappings: you propose (`fin map propose`); the user reviews them.
- Report narrative: describe, don't judge. No recommendations, ratings, or buy/don't-buy language. Numbers in narrative only as inline references, never typed.
- If a command fails, report the error code and run_id. Don't work around a failure.

## Customising

This app is meant to be modified with Claude Code. Keep modules small and readable, and update `PLAN.md` and `CHANGELOG.md` with every change. How to add an account, metric, validation gate or report section, how the schema and tests work, and the module map: `docs/CUSTOMISING.md`.

## Building the tool

- Follow `PLAN.md` phases in order. One phase per session. Plan mode first; wait for approval.
- Python 3.12 via uv. Add dependencies only with `uv add`, and say why. Commit lockfiles.
- macOS is the only target.
- Every failure path raises an error class with a code from `PLAN.md` §9. No bare `except`, no silent defaults.
- Every `fin` command creates a run record and logs through `src/fin/audit/logger.py`. No `print` for logging.
- Database access only through `src/fin/db/conn.py`: short-lived connections, read-only unless the command writes.
- Each document loads in one transaction: stage → validate → commit or roll back.
- Tests required for every change. Only synthetic fixtures in the repo; never commit anything from the data folder.
- Prefer the simplest working design. No speculative features beyond the current phase.
- Finish each phase by updating `CHANGELOG.md` and the status table in `PLAN.md`.

## Quick reference

- Data folder: `~/FinAnalysis` (override `FIN_DATA_DIR`)
- Health check: `uv run fin doctor`
- What is loaded and waiting per company: `uv run fin status`; findings: `uv run fin findings list`
- Tests: `uv run pytest` (add `--golden` for the private real-data set)
- Lint: `uv run ruff check .`
