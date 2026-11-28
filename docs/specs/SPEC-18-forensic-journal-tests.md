# SPEC-18: Forensic foundation and journal-entry tests

## Summary

fin checks that numbers tie; it does not look for entries that were made to mislead. This spec adds the forensic
layer's shared parts (one flags table, a status per test, the Forensic screen, the hand-off of a flag to a query) and
the first tests: journal-entry tests on the Ledger Vouchers fin already loads (the owner's plan, X1). A flag is the
company's to explain: it becomes a query or an FDD finding the user approves; nothing is changed to clear it. No test
scores or rates an entry. Owner decisions and the readings behind the defaults:
[decisions/forensic.md](../decisions/forensic.md).

## Scope

1. **Tables.** `forensic_runs` and `forensic_flags` in a new schema file `db/schema/forensic.sql` (the reading in
   [forensic.md](../decisions/forensic.md)): every run is kept, the latest per test and period is current, and a run
   whose inputs changed is shown as stale. A flag names its source (file and sheet!row, or page), the ledger or party
   (a party as its token), the amount (none for a voucher gap, never 0) and the reason in words.
2. **Inputs on Settings.** `config.toml [forensic]` holds the thresholds, each with its default and reason, and the
   user's inputs (approval limits, holidays, keyword lists), per company in `[forensic.companies.<name>]`.
   `fin forensic settings <co>` shows them; `fin forensic set <co> <key> <value>` changes one, records the actor in
   the audit log and makes the runs it feeds stale. The Settings screen shows and edits them through the same function.
   A test without its input reports "cannot run" and what it needs (owner, 2026-11-28).
3. **Commands.** `fin forensic run <co> [--test T] [--period P]` runs the tests for the open engagement's periods;
   `fin forensic status <co> [--json]` lists every test: ran (with its flag count and how many are at or above the
   threshold), stale, too few entries (with the count and the minimum) or cannot run (and what it needs);
   `fin forensic flags <co> [--test] [--period] [--over-threshold] [--json]` lists flags ordered by amount (the
   owner allows ordering, not scores). All three call one service module that the UI calls too.
4. **The Forensic screen** (Diligence menu): the status list, then the flags by test with drill-down to the file,
   sheet and row (the evidence page fin already serves), a run button (a background job), and on an explicit click
   the hand-off of selected flags to a query (`fin query raise <co> --flags <ids> --question ...`; the assistant
   drafts, the user approves) or to an FDD finding draft (SPEC-21). A flag shows its query and status. Only flags at
   or above the engagement's query threshold can go to a query, as recon items.
5. **Tuning logs** (owner, 2026-11-28: "yes tune later but keep logs"). Each run logs per test through
   `fin.audit.logger`: the thresholds used, the population, the flagged count and the count that fell just outside a
   threshold (within 20% of it), with masked fields only. `fin eval bundle` adds these counts per test (no rows, no
   ledger or party names) so the second client run's bundle carries what tuning needs.
6. **Journal-entry tests** (defaults and minimum populations in [forensic.md](../decisions/forensic.md); single-entry
   tests flag only amounts at or above the query threshold):

   | Test | Flags |
   |---|---|
   | `je_round` | journal lines that are multiples of `round_unit` |
   | `je_weekend` | journal lines dated on a weekend day or a holiday the user keeps, month ends excepted |
   | `je_duplicate` | lines with the same ledger, date, side, amount and voucher type under different voucher numbers |
   | `je_gap` | missing numbers in a numeric voucher series per voucher type and prefix (20 numbers or more) |
   | `je_rare_ledger` | a ledger with at most two lines in a period and none in any other loaded period |
   | `je_unusual_pair` | an (account debited, account credited) pair seen in one rebuilt voucher across all loaded periods |
   | `je_period_end` | journals in the last days of a period on a ledger with none in the same days of the previous period |
   | `je_reversed` | a line in the last days of a period reversed (same ledger and amount, other side) early in the next |
   | `je_sensitive` | journal vouchers touching revenue, cash and bank, trade receivables or trade payables |
   | `je_under_limit` | amounts within `limit_band` below an approval limit the user states |
   | `je_benford` | a Tally group whose first digits do not conform (the digits in excess, with their lines) |

   Pairs and set-offs read vouchers rebuilt from every ledger block (type, number, date within a file), never
   `particulars` ([forensic.md](../decisions/forensic.md), questions 1 and 2). Accounts come from confirmed mappings;
   a ledger without one is named as itself and the flag says the mapping is unconfirmed.
7. **Planted cases.** `mock/generator/data.py` (PR #141) gets, per test, at least one planted row and one near miss
   just outside the default; `mock/EXPECTED.md` lists them. An existing mock row a test rightly flags (the auditor's
   adjustment AJ-1 is a sensitive journal) is listed in `EXPECTED.md` with why; a routine row that flags means the
   default is too loose and is raised (recorded in the decisions file), never a generator change to hide it.
8. **Docs.** `docs/product.md` (a Forensic section and the command coverage table), `docs/errors.md` (codes in the
   range E990 to E999), `docs/development.md` (how to add a forensic test).

Out of scope: narration and user tests (X2, backlog); any per-entry score or ranking; a gate on forensic runs;
changing a figure to clear a flag.

## Acceptance criteria

- [ ] On the mock set, each test flags exactly the rows `mock/EXPECTED.md` lists for it, and no near miss.
- [ ] A test below its minimum population reports "too few entries" with the count and flags nothing; a test without
      its input (no approval limit stated) reports "cannot run" and what it needs, on the CLI and the screen.
- [ ] Loading another Ledger Vouchers file or changing a forensic setting marks the affected runs stale; running again
      keeps the earlier flags and any query on them.
- [ ] `fin query raise --flags` refuses a flag below the threshold or already in an open query (an E99x code), and a
      raised query shows on its flags.
- [ ] No flag, log line, export or bundle holds a masked value; the masking registry test covers `forensic_flags`.
- [ ] The run log holds, per test, the thresholds, population, flagged and just-outside counts; `fin eval bundle`
      carries them without names.
- [ ] Every new command has a row in the command coverage table (`tests/unit/test_command_coverage.py`).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add the forensic flags, settings, status and Forensic screen with the journal-entry tests | `db/schema/forensic.sql`, `src/fin/forensic/`, CLI, `src/fin/ui`, `mock/generator`, `mock/EXPECTED.md`, docs | all criteria |

Risk: high (a schema file, masking, the query hand-off). One PR (owner); a separate reviewer.
