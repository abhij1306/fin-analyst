# fin-analyst — rules for Claude Code

Local financial health analysis tool. Ingests company financial documents into DuckDB, answers questions with evidence, generates reports and dashboards. Full spec: `PLAN.md`.

## Using the tool

- Use `fin` (`uv run fin ...`) for all data access. Change data only when the user asks, and through `fin` (`fin sql --write`, `fin review`, `fin delete`), which backs up and logs. If the guard asks for confirmation, that is the user's call.
- Every number you state must come from `fin` output. Never do arithmetic in prose; ratios and growth are computed in SQL.
- Answers to questions use this format: answer, calculation, source table (file + sheet!cell or page), SQL, flags.
- Flag unconfirmed mappings, failed validations, and missing data. Never fill gaps with assumptions.
- Mappings: you propose (`fin map propose`); the user reviews them.
- Report narrative: describe, don't judge. No recommendations, ratings, or buy/don't-buy language. Numbers in narrative only as inline references, never typed.
- If a command fails, report the error code and run_id. Don't work around a failure.

## Customising

This app is meant to be modified with Claude Code. Keep modules small and readable, and update `PLAN.md` and `CHANGELOG.md` with every change.

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
- Tests: `uv run pytest` (add `--golden` for the private real-data set)
- Lint: `uv run ruff check .`
