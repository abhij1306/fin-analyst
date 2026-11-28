# Recurring findings

Mistakes made more than once in this repo, and what now prevents each. The orchestrator updates this file in the
retro at the end of each spec ([process.md](../process.md)). A finding gets a check where one can catch it; a rule
in [engineering.md](../engineering.md) only where none can. Builders read this before opening a PR.

"SPEC-00 PR n" means the check is planned there and the rule is applied by self-review until it merges.

| Finding | Category | Seen in | Prevented by |
|---|---|---|---|
| A missing or unreadable input was counted as zero or dropped silently | `missing-as-zero` | #69, #75, #78, #100, #102 | Rule 1; a test with incomplete input for each new aggregate view |
| An approved gate stayed approved after data under it changed | `gate-input` | #77, #84, #85, #105 | Check: gate-input registry test (`tests/unit/test_gate_inputs.py`) |
| A name reached output unmasked, or masking rules were rebuilt | `masking` | #69, #73, #82, #101, #112 | Check: masking registry test (`tests/unit/test_mask_registry.py`) |
| Document text was written into HTML, Markdown or Excel without escaping | `escaping` | #81, #102, #104, #105 | Check: `fin.output.escape` and `tests/unit/test_guardrails_structure.py` |
| Docs or comments said something the code does not do | `docs-drift` | #48, #50, #62, #93, #104, #105 | Check: `tests/unit/test_cli_consistency.py`; rule 23 |
| A command accepted an option it ignored, or exited 0 without doing the work | `cli-contract` | #13, #54, #93, #107 | Check: `tests/unit/test_cli_consistency.py` |
| Two runs shared a temporary path, or checked then wrote | `temp-race` | #13, #34, #54, #64, #67, #90 | Check: `fin.paths.atomic_write`, ruff `S108`, fixed-temporary-name test |
| A guard that matched command text was bypassed | `guard-bypass` | #45, #51, #69, #102 | Rule 10: refuse in the function; bypasses kept as tests |
| The oldest or a duplicate record was used instead of the current one | `stale-version` | #76, #80, #81, #101 | Rule 3; a load-twice test for each new reader |
| Units, currencies or signs were mixed | `unit-or-sign` | #76, #80, #85, #105 | Rule 2; SPEC-06 PR 1 |
| A path from input left its folder | `path` | #37, #108 | Check: `fin.paths.inside` (E013) |
| A function passed Sonar's complexity or parameter limit and was cleaned up later | `complexity` | #37, #87, #107 | Check: ruff `C901`, `PLR0913`, `PLR0915` |
| A behaviour change left another area's test failing | `test-left-behind` | 5 of 8 FDD branches | Rule 21 (no check found yet) |
| Tests that skip without an optional extra ran in no CI job | `ci-config` | UI chat and format tests | Check: the CI `ui` job runs every UI test |
| Bot findings arrived after the merge and became fix PRs | process | #95 to #102, fixed in #105 | [process.md](../process.md) step 6: wait for the bots once |
| PRs over 1,500 lines drew most later fixes | process | 31 of 113 PRs | [process.md](../process.md) step 5: a separate reviewer over 1,500 lines |
| PRs of two or three files, one per spec row, cost more than they saved | process | owner correction, 2026-11-28: "have u created like 2-3 files PRs ? thats just a waste of time" | [process.md](../process.md) step 2: one PR per spec, or per group of rows that ship together |
| A product rule was rebuilt after the owner's decision arrived late | process | masking, 2026-11-26 | Owner decisions are read before a phase ([specs](../specs/README.md)) |
| The assistant wrote straight into `facts` to get past a missing figure | `guard-bypass` | client run, 2026-11-27 | SPEC-08 PR 2 |

PR numbers for the first 13 rows come from a read of all review-bot comments on 2026-11-28; they are examples, not
a full list.

## Measures

One row per finished spec, added in its retro. The first row is the state before this process.

| After | PRs | Fix PRs | Bot findings | Red CI runs | Median changed lines | Sonar open in src |
|---|---|---|---|---|---|---|
| #1 to #114 (baseline) | 113 | 31 | 389 | 10 of the last 100 | 855 | 8 |
