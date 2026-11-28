# SPEC-02: Shared services and background jobs

## Summary

The UI is becoming the main interface. Today each UI button repeats the glue its CLI command has (refresh analytics,
recheck held documents, format output), and every handler runs to the end before the page answers. This spec gives
both interfaces one function per action and gives the UI a way to run long work with progress. It adds no screen.

## Scope

- **Services.** One function per user action in `fin.services` (start with the 12 decisions in
  `src/fin/ui/actions.py` and with ingest). It takes typed arguments, does the work inside an audited run and
  returns a typed result. The CLI command and the UI handler are thin: parse, call, render.
- **Actor.** Who did it is passed in (`user`, `ai:<provider>`), not read from the `CLAUDECODE` environment
  variable. Stored values change from `claude` to `ai:claude`; reports and counts read the prefix. The UI must
  record a button press as `user` even when `fin ui` was started from inside Claude Code.
- **Jobs.** A long action (ingest, report, export, dashboard build) runs as a job: one worker thread, a `jobs` table
  in `audit.duckdb` (id, action, arguments, state, progress line, run id, started, ended), and a page fragment that
  shows progress and the result. One job at a time per company; a second is queued. Jobs running when the server
  stops are marked "interrupted" at the next start.

Out of scope: more than one user, a job broker, scheduled jobs.

## Design notes

- The data shape comes first: `ActionResult = Done | Refused(code, message) | NeedsDecision(question)`. The UI
  renders each case; the CLI maps each to an exit code. No handler catches a bare exception to build a message.
- There are no migrations ([development.md](../development.md)): the stored actor values change in the schema file
  and the client re-creates or re-ingests.

## Acceptance criteria

- [ ] `actions.py` holds no logic of its own: each entry names a service function.
- [ ] A test presses each decision as the UI and as the CLI and gets the same run record apart from the interface.
- [ ] A test starts `fin ui` with `CLAUDECODE=1` and a button press is recorded with actor `user`.
- [ ] A job survives a page reload; an interrupted job is shown as interrupted.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Move the decision actions into shared service functions | `src/fin/services`, `src/fin/ui/actions.py`, CLI callers | parity test green |
| 2 | Pass the actor explicitly and store it as user or ai:provider | `src/fin/map`, `ingest.py`, schema, status and eval counts | actor tests green |
| 3 | Add background jobs with progress to the UI | `src/fin/services/jobs.py`, `audit.sql`, UI partial | job tests green |

Risk: PR 2 changes stored values across several modules. It gets a separate reviewer.
