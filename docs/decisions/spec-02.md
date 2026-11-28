# SPEC-02: shared services, explicit actor, background jobs

Builder readings of [../specs/SPEC-02-services-and-jobs.md](../specs/SPEC-02-services-and-jobs.md), built in one PR
(the owner widened the row to all three, 2026-11-28). The owner confirms or changes them. Format: [README.md](README.md).

## 1. A button shows what the CLI prints

- **Decision**: a decision's lines come from its service, so the UI's alert shows the same lines as the CLI. Saving a
  finding as is now also shows the "metrics: shared/analytics.duckdb refreshed" line in the UI, and marking an FDD
  finding final shows the finding as `fin fdd finding list` does.
- **Why**: "the same run record apart from the interface"; one text per action means one place to change it.
- **How to change it**: `src/fin/services/decisions.py` (each service's `body`).

## 2. The run's arguments are the service's, with `via` and `actor`

- **Decision**: a decision's run records the arguments its service takes plus `via` (`cli` or `ui`) and `actor`.
  `fin review confirm|reject` of mapping items no longer lists the `--ledgers`, `--customers`, ... flags in its run
  (those kinds still run as before; they are not in the UI).
- **Why**: the parity test compares the two run records; the interface is the only difference.
- **How to change it**: `src/fin/services/decisions.py` (`args`), `src/fin/map/commands.py` (`review_confirm`).

## 3. Only the CLI reads `CLAUDECODE`

- **Decision**: the CLI turns `CLAUDECODE` into the actor at its boundary (`ai:claude`, else `user`) and passes it
  down; services and mapping modules take it as an argument; the UI always passes `user`. A proposal is always
  `ai:claude`; `on_request` is true for any other decision an AI records.
- **Why**: the spec says the actor is passed in, not read from the environment; the CLI has no other way to know that
  Claude Code runs it.
- **How to change it**: `src/fin/actor.py` (`from_environment`, `recorded`).

## 4. Every AI-read row says `ai:claude`, not only facts

- **Decision**: `read_by` changes from `claude` to `ai:claude` for facts from a reading and for contracts,
  walkthroughs and disclosures. Readers test the `ai:` prefix; listings name it "Claude". Old data folders are
  re-created and re-ingested (no migration).
- **Why**: one convention for every stored actor value; the spec's "stored values change from claude to ai:claude".
- **How to change it**: `src/fin/actor.py` (`CLAUDE`, `label`) and the SQL in `status.py` and `evals/counts.py`.

## 5. `NeedsDecision` waits for its first caller

- **Decision**: `ActionResult` is `Done | Refused`; `NeedsDecision(question)` is added by the first action that asks
  the user something (SPEC-04 PR 1, the unit question).
- **Why**: no action of this spec asks a question; a type with no producer would have no test.
- **How to change it**: `src/fin/services/result.py`.

## 6. One worker for all companies; the first job is Export to Excel

- **Decision**: one worker thread runs every job in order, so a job of another company also waits. The Reports screen
  gets **Export to Excel** (`fin export`) as the first job, with the Jobs card. Ingest, report and dashboard buttons
  come with their screens (SPEC-03, SPEC-11) and are added to `WORK`.
- **Why**: the spec names one worker thread and no new screen; the export is the long action the Reports screen
  already lists the output of.
- **How to change it**: `src/fin/services/jobs.py` (`Jobs`), `src/fin/ui/job_routes.py` (`WORK`, `LABELS`).

## 7. A page waits for a job's write as for another command

- **Decision**: a connection this process has open in the other mode (a page reading while a job writes) is retried
  like a lock held by another process, then E501.
- **Why**: DuckDB refuses a second connection with a different mode in one process; jobs make that common.
- **How to change it**: `src/fin/db/conn.py` (`_LOCK_MARKERS`).
