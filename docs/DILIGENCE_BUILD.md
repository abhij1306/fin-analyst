# Diligence build: phase D1 tracker

Builds the part of [DILIGENCE_PLAN.md](DILIGENCE_PLAN.md) that needs no answers from the open questions (§9):
engagement, accountant queries, adjustments, EBITDA bridge and EBITDA-to-cash walk on the audited statements, and
gates. Everything is greenfield: new tables go straight into the schema files, with no migrations (owner,
2026-11-09).

## Tracker

| PR | Scope | Builder | Depends on | Status | Link |
|---|---|---|---|---|---|
| 1 | Foundation: config, schema, engagement, queries, adjustments, CLI, rules | Opus (medium) | none | Merged | [#43](https://github.com/abhij1306/fin-analyst/pull/43) |
| 2 | EBITDA bridge and EBITDA-to-cash walk (views and `fin engagement bridge`) | Opus (medium) | 1 | Merged | [#44](https://github.com/abhij1306/fin-analyst/pull/44) |
| 3 | Gates: `fin gate`, refusals, hook, `fin status` summary | Opus (medium) | 1 | Merged (G2 hashes wording and draft state only, so sending or closing a query does not reopen it) | [#45](https://github.com/abhij1306/fin-analyst/pull/45) |
| 3a | Tests: share one ingest of the clean companies; test job timeout 10 min | main session | none | Merged | [#46](https://github.com/abhij1306/fin-analyst/pull/46) |
| 3b | CI: split the test job across 3 runners (pytest-split) | main session | none | Merged | [#47](https://github.com/abhij1306/fin-analyst/pull/47) |
| 4 | Docs: DILIGENCE_PLAN.md and README updated to what was built; PLAN.md section | main session | 2, 3 | Merged (wording: [#50](https://github.com/abhij1306/fin-analyst/pull/50)) | [#48](https://github.com/abhij1306/fin-analyst/pull/48) |
| 5 | Sonar: fix the 3 real issues (S8786 regexes in `map/normalize.py` and `report/lint.py`, S3776 `doctor`); the 19 S8541 are false positives the owner marks in SonarCloud. Also fix any new Sonar issues PRs 1–3 raise | Sonnet (medium) | 4 | Merged | [#49](https://github.com/abhij1306/fin-analyst/pull/49) |
| 6 | Review: Opus (high) code review of PRs 1–5, 3a and 3b as one diff (`f753747..main`; everything before today, including #41 Docling and #42, is already reviewed), then fixes for what it confirms: `fin sql --write` refuses the diligence tables (E806), the hook asks for anything that looks like a gate approval, G1 reopens when a figure changes, amount and option checks | Opus (high) review, fixes by a builder | 5 | In review | this PR |

## Decisions (fixed for this build)

- **Greenfield.** Tables go into `src/fin/db/schema/diligence.sql` (tables) and `diligence_views.sql` (views),
  run after `company_views.sql` for every company database. Audit events go into `audit.sql`. There is no migration
  code and no "add missing tables" step.
- **Naming.** "Accountant queries" are table `accountant_queries` and CLI `fin query`, so they don't clash with the
  audit table `queries` (which logs `fin sql`).
- **One open engagement per company.** It holds the periods in scope and the query threshold (from `config.toml`
  `[diligence] query_threshold`, default 100000.00 rupees, or `--threshold`).
- **Adjustments are EBITDA effects.** Each has a period, scope, category (from `[diligence] adjustment_categories`),
  account (optional, the dictionary account it relates to), signed amount in rupees (its effect on EBITDA), rationale
  and evidence (a query id, finding id or source reference: at least one). Reported facts are never changed. Adjusted
  EBITDA is a view.
- **Queries are drafted, never sent.** Status flow: `draft → approved → sent → answered → closed`, and `withdrawn`
  from any status before `closed`. `sent` is set by the user (fin does not send anything). There is no export in
  this build (it waits on open question Q5).
- **Gates** are a small registry in code (`fin/diligence/gates.py`), approved in order. A gate counts as approved
  only while the hash of its inputs equals the hash recorded at approval. When data changes, it reopens on its own.
  Gates in this build:
  - **G1 Data:** every annual period in scope has a loaded document, no held documents, no pending mappings. Its
    inputs include the figures (`current_facts`), so a changed figure reopens it.
  - **G2 Queries:** no queries in `draft`. Unlocks `fin adjust accept|reject`.
  - **G4 Adjustments:** no `proposed` adjustments, and every accepted one has evidence. Unlocks
    `fin engagement close`.
  - G3 (recast) and G5 (report) come with D6 and D7.
- **Only the user approves a gate.** A PreToolUse hook asks the user before any Bash command that looks like a
  gate approval, and `fin sql --write` cannot write gate approvals (E806).
- **Error codes:**

  | Code | Meaning | Exit code |
  |---|---|---|
  | E801 | A gate this command needs is not approved, or reopened | 3 |
  | E802 | Gate cannot be approved: its checklist fails or an earlier gate is not approved | 3 |
  | E803 | No open engagement, or one is already open | 2 |
  | E804 | No evidence, category not in config, period not in the engagement, or unknown account | 2 |
  | E805 | Unknown query or adjustment id, or a status change not allowed | 2 |
  | E806 | `fin sql --write` given a statement that writes to a diligence table | 2 |

## Done when (phase D1)

On a synthetic company with statements loaded, Claude can:
- open an engagement;
- raise a query from an E404 finding and take it through draft → approved → sent → answered → closed;
- propose an adjustment with evidence and see the EBITDA bridge and the EBITDA-to-cash walk;
- be refused (E801) when accepting the adjustment before G2 is approved;
- be asked by the hook when approving a gate;
- see the gate reopen after the data under it changes;
- close the engagement only after G4 is approved.

CI is green. README, CLAUDE.md and `/diligence` describe all of it.
