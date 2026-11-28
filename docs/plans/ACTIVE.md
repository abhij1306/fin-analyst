# Implementation tracker

One row per PR-plan row of each spec ([specs/README.md](../specs/README.md)). A PR covers a whole spec, or a group
of its rows that can ship together (the CLI rows, then the UI rows); it is split only where a later row waits on
another spec (owner, 2026-11-28; [process.md](../process.md) step 2). A PR marks each of its rows `done (#n)`. Work in flight is read from open PRs, not from this file.

Statuses: `todo`, `done (#n)`, `blocked (Qn)` (waits on an owner decision), `human`, `deferred`.

Updated: 2026-11-28.

## Human-only tasks

| # | Task | Needed by | Status |
|---|---|---|---|
| H1 | Switch off the two test-style rules (S9073, S5778) in the SonarCloud profile and make the Sonar gate a required check | SPEC-00 PR 7 | todo |
| H2 | Read the owner decisions Q1 to Q13 in [specs/README.md](../specs/README.md) | before each spec starts | todo |
| H3 | Ask the client for a second run after specs 01, 04 and 13 | SPEC-13 | todo |
| H4 | Get the column headings (not the data) of the client's real register, ledger, trial balance and GST summary exports | SPEC-13 PR 2 | todo |
| H6 | Get the adviser's FDD inquiry list from the client; it goes in the client's data folder, never the repo | SPEC-17 PR 3 | todo |
| H5 | Say when SPEC-16 (AI providers) may start; it is on hold | SPEC-16 | todo |

## Current focus

- Exit predicate: every row of SPEC-00 and SPEC-01 done, `main` green.
- Can start together: SPEC-00 PR 1, SPEC-01 PR 1, SPEC-07 PR 1, SPEC-08 PR 1, SPEC-12 PR 1, SPEC-13 PR 1.
- Then: SPEC-02, which the UI specs (03, 05, 06, 09, 11) wait for; and the client's chain 13 PR 2, 14 PR 1 and 2,
  10 PR 1 and 2, 15 PR 1 and 2, whose CLI rows do not wait for the UI.
- SPEC-17 PR 1 to 4 depend on nothing and can start with the first wave; PR 5 waits for SPEC-15 PR 1.
- On hold: SPEC-16, until the owner says so (owner, 2026-11-28).

## Rows

| Spec | PR | Title | Risk | Status |
|---|---|---|---|---|
| 00 | 1 | Run every UI test in CI and macOS checks on install changes | | todo |
| 00 | 2 | Turn on complexity, argument and security lint rules | | todo |
| 00 | 3 | Add the type check job | | todo |
| 00 | 4 | Add shared escaping, path and atomic-write helpers | | todo |
| 00 | 5 | Add the gate-input and masking registries with their tests | high | todo |
| 00 | 6 | Add the CLI and docs consistency tests | | todo |
| 00 | 7 | Clear the open Sonar findings in src and add the categories tally | | todo |
| 01 | 1 | Fix the unit line, evidence crash, negative amounts and empty lists from the client run | | done (#116) |
| 02 | 1 | Move the decision actions into shared service functions | | done (#118) |
| 02 | 2 | Pass the actor explicitly and store it as user or ai:provider | high | done (#118) |
| 02 | 3 | Add background jobs with progress to the UI | | done (#118) |
| 03 | 1 | Run ingest from the Files screen with one outcome row per file | | todo |
| 03 | 2 | Show a next step for every ingest failure | | todo |
| 03 | 3 | Add company creation, re-ingest and delete to the Files screen | | todo |
| 03 | 4 | Start an assistant reading from a failed file | | todo |
| 04 | 1 | Hold a document with no unit line and ask for the unit | high | todo |
| 04 | 2 | Read .xls workbooks | | todo |
| 04 | 3 | Hold only the sheet with unlabeled values and name the cells | | todo |
| 04 | 4 | Offer a PDF add-on when pages are skipped | | todo |
| 05 | 1 | Add the indirect cash flow labels to the dictionary seed | | todo |
| 05 | 2 | Require low-confidence mappings to be confirmed one at a time | | todo |
| 05 | 3 | Recheck after a mapping change and name the mapping behind a finding | | todo |
| 05 | 4 | Complete the Mappings screen | | todo |
| 05 | 5 | List the checks that did not run and what they need | | todo |
| 06 | 1 | Add display unit settings and convert in SQL | high | todo |
| 06 | 2 | Add the databook layout to statements and the Excel export | | todo |
| 06 | 3 | Add the Statements screen | | todo |
| 06 | 4 | Use the display settings in reports and evidence pages | | todo |
| 07 | 1 | Add fin schema and name the available columns in E504 | | todo |
| 07 | 2 | Add a fact sources view | | todo |
| 07 | 3 | Add saved metrics | | todo |
| 08 | 1 | Add fin fact state for figures the user states | high | todo |
| 08 | 2 | Refuse direct writes to facts and name the missing input of a metric | | todo |
| 08 | 3 | Mark stated figures in statements, evidence, reports and gate G1 | high | todo |
| 09 | 1 | Import adjustments from a sheet | high | todo |
| 09 | 2 | Add the engagement and bridge screens | high | todo |
| 09 | 3 | Add the adjusted P&L view and screen | high | todo |
| 09 | 4 | Complete the Queries and Gates screens | high | todo |
| 10 | 1 | Store third-party reference tables apart from company facts | high | todo |
| 10 | 2 | Map reference lines and compare them with fin's figures | | todo |
| 10 | 3 | Add the comparison screen and the query and adjustment hand-offs | | todo |
| 11 | 1 | Generate and download reports from the Reports screen | | todo |
| 11 | 2 | Add Settings and Health screens | | todo |
| 11 | 3 | Add the Questions screen and Log filters | | todo |
| 11 | 4 | Add FDD finding drafts and the dashboard build | | todo |
| 11 | 5 | Group the navigation and add the command coverage table with its test | | todo |
| 12 | 1 | Install Quarto without sudo on macOS and stop retrying non-network failures | high | todo |
| 12 | 2 | Use uv's managed Python and warn about temporary folders | high | todo |
| 12 | 3 | Install the UI by default and end the install with its link | high | todo |
| 13 | 1 | Scan a data room into a plan: unpack, drop duplicates, name each file's kind | high | done (#117) |
| 13 | 2 | Load an approved data-room plan in one run | | done (#117) |
| 13 | 3 | Show and approve the data-room plan on the Files screen | | todo |
| 14 | 1 | Rebuild the P&L and balance sheet from the trial balance for every year | high | todo |
| 14 | 2 | Derive an indirect cash flow from the rebuilt balances | high | todo |
| 14 | 3 | Show reported, rebuilt and difference in statements and on the Statements screen | | todo |
| 15 | 1 | Join the reconciliations into one discrepancies view with levels and parents | high | todo |
| 15 | 2 | Add fin discrepancies and the export sheet | | todo |
| 15 | 3 | Add the differences screen with drill-down and query hand-off | | todo |
| 16 | 1 | Refuse decisions from an AI actor in the service layer | high | deferred |
| 16 | 2 | Add the provider setting, key storage and consent record | high | deferred |
| 16 | 3 | Add a provider client with reading, mapping and narrative tasks | high | deferred |
| 16 | 4 | Put the chat behind a backend interface | high | deferred |
| 16 | 5 | Add the API chat backend with fin's tools | high | deferred |
| 16 | 6 | Move the skills' instructions into provider-neutral prompts | high | deferred |
| 17 | 1 | Mask bank account numbers | high | done (#118) |
| 17 | 2 | Value USD orders at a rate the user states | high | done (#118) |
| 17 | 3 | Read the inquiry list from the data folder when present | | done (#118) |
| 17 | 4 | Make credit terms 90 days by default and settable per engagement | high | done (#118) |
| 17 | 5 | Show the recast against the books in the differences list | | todo |
