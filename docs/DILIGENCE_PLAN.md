# Diligence extension — plan (draft)

Status: draft for review, 2026-11-09. Not yet in `PLAN.md`; move it there phase by phase as each is agreed.

Source: a review of fin-analyst against a quality-of-earnings (QoE) diligence handoff. We have the review, not the
handoff itself, so every requirement below is inferred from it. The open questions in §9 need answers from the
handoff's author before phase D1 starts.

## 1. Goal

fin-analyst today analyses **published statements** (P&L, balance sheet, cash flow). This plan extends it to the
**transactions behind them**, for a diligence engagement on one company:

| Need | Today | After this plan |
|---|---|---|
| Audited statements, checks, metrics | Yes | Unchanged: the statement layer |
| Sales and purchase registers, Tally ledgers, trial balance, bank, payroll | No | Loaded with source file + sheet!row lineage (D2, D3) |
| Reconciliation: register ↔ ledger ↔ accounts | No | D4 |
| Reconciliation: bank ↔ receipts ↔ invoices | No | D5 |
| Contract-period revenue recast from contract evidence | No | D6 |
| Proposed adjustments kept separate from reported numbers | No | Adjustments layer (D1, D7) |
| Accountant queries (Rs 1 lakh threshold, drafted for approval) | Partly (findings) | Queries layer (D1, D8) |
| Gates that stop the build for review | No | Gates (D1) |
| EBITDA-to-cash walk, adjusted EBITDA, normalised working capital | Metrics only | D7 |
| Customer, product, sector, pricing, retention analytics | No | D9 |

## 2. Rule changes

These replace the owner rules of 2026-11-06/07 **for diligence data**. The statement layer keeps working as today.

1. **Differences are queried, never fixed.** A reconciliation difference is the company's, not fin's. It stays as
   found. It is closed only by an accountant's answer (`fin query answer`) or by an adjustment the user accepts. No
   command edits transaction data to make a reconciliation tie.
   - Not affected: fixing **fin's own** errors (a wrong mapping, a misread column) is still allowed and audited.
     The rule is about the company's numbers, not fin's reading of them.
   - Statement findings (E401–E405) can be raised as queries (`fin query raise --finding <id>`).
2. **Gates stop the build.** The work runs in stages. Each stage ends at a gate. The next stage's commands refuse
   (exit 3, E801) until the user approves the gate. Claude may prepare everything for a gate but never approves one:
   `fin gate approve` is the one command the PreToolUse hook asks the user about (reintroduced for this command
   only). All other commands keep running without prompts, with backup and audit log as today.
3. **Queries are drafted, never sent.** Claude drafts query wording. The user approves it. fin exports the approved
   queries as a letter (Excel and Word) for the user to send. fin never sends anything.

`CLAUDE.md` gets a "Diligence" section with these three rules when D1 lands.

## 3. Concepts

- **Engagement**: one diligence job on one company: the periods in scope (e.g. FY24–FY26), the materiality
  threshold (default Rs 1,00,000, `config.toml [diligence] query_threshold`), and its gates. It lives in the
  company database, next to the statement data. One open engagement per company.
- **Source ledgers**: the transaction-level data (§4). Each row keeps the file sha256 and sheet!row it came from.
  Raw files are archived untouched, as today.
- **Reconciliation**: a fixed, named comparison between two sources for a period, e.g. "sales register vs GL
  sales, FY25, monthly". It produces matched totals and differences. Each difference above the threshold becomes a
  draft query. Differences below it are listed and summed (so many small ones are still visible).
- **Query**: a question to the company's accountant, with the amounts, sources and difference. Status: `draft` →
  `approved` → `sent` → `answered` → `closed` (or `withdrawn`).
- **Adjustment**: a proposed change to a reported figure (normalisation, reclassification, recast, error), by
  account and period, linked to its evidence (query, reconciliation difference, contract). Status: `proposed` →
  `accepted` / `rejected`. Reported figures are never changed: adjusted figures are a view (reported + accepted
  adjustments).
- **Gate**: a named checkpoint with a fixed checklist that fin evaluates (e.g. "no draft queries left
  unreviewed"). The user approves it. The approval is recorded with what it covered (data hash).

## 4. Data model (company database, new schema file `diligence.sql`)

Same conventions as `company.sql`: DECIMAL(20,2) rupees, no foreign keys, ids as links, a `run_id` on every row,
one schema file, no migrations.

| Table | Holds |
|---|---|
| `engagement` | engagement_id, periods in scope, threshold, status, opened run_id |
| `source_files` | file_id, kind (`trial_balance`, `ledger`, `sales_register`, `purchase_register`, `bank`, `payroll`, `contract`), file name, sha256, archive path, period covered, profile used, status |
| `ledger_accounts` | Tally ledger name, group, parent group, mapped Schedule III account (proposed by Claude, reviewed like label mappings) |
| `trial_balance` | file_id, sheet, row, period_end, ledger, opening, debit, credit, closing |
| `ledger_entries` | file_id, sheet, row, date, voucher type, voucher no, ledger, counter-ledger, party, debit, credit, narration |
| `register_lines` | file_id, sheet, row, kind (sales/purchase), date, invoice no, party, GSTIN, item, quantity, rate, taxable value, tax, total |
| `bank_lines` | file_id, sheet, row, account, date, description, reference, debit, credit, balance |
| `payroll_lines` | file_id, sheet, row, month, employee id (as given), department, gross, deductions, net, employer costs |
| `contracts` | contract_id, customer, start, end, value, billing terms, recognition basis, source (file + page), read_by |
| `recon_runs` | recon_id, name, period, left source, right source, left total, right total, difference, run_id |
| `recon_items` | recon_id, key (month / party / invoice), left value, right value, difference, left_source, right_source, status (`matched`, `difference`, `left_only`, `right_only`) |
| `queries` | query_id, engagement_id, origin (recon item, finding, manual), question text, amounts, sources, status, answer text, answer source, timestamps, run_ids |
| `adjustments` | adj_id, category, account, period, amount, rationale, evidence links (query_id / recon_id / contract_id), status, decided run_id |
| `gates` | gate, checklist result JSON, status (`open`, `approved`), approved_at, data hash, run_id |

Views: `adjusted_facts` (current_facts + accepted adjustments), `adjusted_metrics` (the metric set on adjusted
facts, for EBITDA, margins and working capital), `ebitda_to_cash` (EBITDA → change in working capital → tax → capex
→ CFO, reported and adjusted), `nwc_monthly` (from the trial balance, when monthly TBs are loaded).

Audit (`shared/audit.duckdb`, append-only): `query_events`, `adjustment_events` and `gate_events` record every
status change, in the same way `findings` does.

## 5. Stages and gates

| Gate | Stage before it | Checklist fin evaluates | Unlocks |
|---|---|---|---|
| G1 Data | Load statements, TB, ledgers, registers, bank, payroll | Every period in scope has a TB and statements; every TB ledger is mapped (confirmed); TB ties to the statements by account (differences become queries) | Reconciliation |
| G2 Reconciliation | D4 + D5 reconciliations | Every required reconciliation ran for every period; every difference above the threshold has a query (draft reviewed) | Recast and adjustments |
| G3 Recast | D6 contract recast | Every contract above the threshold has a schedule; recast differences have proposed adjustments | Adjustments |
| G4 Adjustments | D7 adjustments | No `proposed` adjustments left; every accepted one has evidence | Report |
| G5 Report | Diligence report | Report rendered from data that matches the G4 data hash | Close |

A gate approval records the data hash. If data under an approved gate changes later (a new file, an answered query
that becomes an adjustment), the gate reopens and fin says why. The user approves it again.

## 6. Commands

The command count stays small: four new command groups and one ingest option.

| CLI | Claude Code | Who |
|---|---|---|
| `fin engagement open <co> --periods FY24,FY25,FY26 [--threshold]`, `fin engagement status <co>` | `/diligence` | Either |
| `fin ingest <co> <file> --kind trial_balance\|ledger\|sales_register\|purchase_register\|bank\|payroll [--profile]` | `/ingest` | Either |
| `fin map ledgers <co>` (propose/confirm Tally ledger → account, through the existing review flow) | `/review` | Claude proposes, user reviews |
| `fin reconcile <co> [name] [--period]` | `/reconcile` | Either |
| `fin query list\|raise\|draft\|approve\|answer\|close\|export <co>` | `/queries` | Claude drafts; `approve` and `close` when the user says |
| `fin adjust propose\|accept\|reject\|list <co>` | `/adjust` | Claude proposes; accept/reject when the user says |
| `fin gate status\|approve <co> <gate>` | `/diligence` | `approve`: user only (hook asks) |
| `fin report <co> --diligence` | `/report` | Either |

Error codes: E8xx (unused today). E801 gate not approved, E802 unknown column profile, E803 file period outside the
engagement, E804 adjustment without evidence, E805 query status change not allowed, E806 data changed under an
approved gate.

## 7. Phases

One PR per phase, each small enough to review in one sitting. Each has tests on synthetic fixtures only.

### D0: Confirm requirements (no code)
Answer §9 with the handoff's author. Get one anonymised sample of each export format (Tally TB, day book or ledger,
sales register, bank statement, payroll), or its column headers. Build the synthetic fixtures from them.

**Done when:** §9 is answered and fixture column layouts are agreed.

### D1: Foundation: engagement, gates, queries, adjustments
- `diligence.sql`, created by `fin init` for new company databases. Existing databases get the tables through
  `fin init` creating missing tables. This is the one exception to "no migrations": it only adds tables, never
  alters existing ones. (Confirm with the owner.)
- `fin engagement`, `fin gate`, `fin query` (raise from a finding, manual, approve, answer, close), `fin adjust`.
- Hook: PreToolUse `ask` on `fin gate approve` only.
- Views `adjusted_facts`, `adjusted_metrics`, sourced from statement facts. This is already useful on statements
  alone (e.g. normalising a one-off item).
- `CLAUDE.md` "Diligence" rules, README error codes, `/diligence`, `/queries`, `/adjust` commands.

**Done when:** a synthetic company with statements can open an engagement, raise a query from an E404 finding,
propose and accept an adjustment, see adjusted EBITDA, and is refused (E801) at an unapproved gate.

### D2: Trial balance and Tally ledgers
- A tabular loader (`fin/extract/ledger.py`) driven by column **profiles** (`templates/profiles/*.toml`: header
  names → fields, date format, debit/credit or signed amount, unit). Ship profiles for Tally's standard Excel
  exports. An unknown layout fails with E802 and lists the headers it found, so Claude can write a profile.
- TB ledgers map to Schedule III accounts (`ledger_accounts`), proposed by Claude and confirmed in review.
- Built-in reconciliation `tb_vs_statements`: TB closing balances rolled up by account vs the audited statement
  facts, per period.
- Gate G1 checklist.

**Done when:** synthetic TB + day book load with sheet!row lineage; a planted TB-vs-statement difference above the
threshold becomes a draft query, and one below it is listed in the "below threshold" total.

### D3: Registers, bank, payroll
- Profiles for sales and purchase registers, bank statements (Excel/CSV; PDF bank statements through a Claude
  reading, like statement pages), and payroll.
- Duplicate and overlap checks: the same invoice number twice, overlapping bank periods, a gap in bank balances
  (closing ≠ next opening). These are findings, then queries.

**Done when:** each synthetic file loads, and the planted duplicate invoice and bank balance gap are found.

### D4: Register ↔ ledger ↔ accounts reconciliation (handoff Module 2)
- `sales_register_vs_gl`, `purchase_register_vs_gl` (monthly, then by party), `gl_vs_tb` (ledger movements vs TB),
  `payroll_vs_gl`. Deterministic SQL, one function per reconciliation in `fin/reconcile/`.
- Differences above the threshold → draft queries.

**Done when:** every planted difference in the synthetic set is found, with both sources, and nothing else is.

### D5: Bank ↔ receipts ↔ invoices (handoff Module 4)
- Matching in fixed passes: exact reference, then amount + party + date window (config), then amount + date
  window. Each match records the pass that made it. Unmatched items become `left_only` / `right_only`.
- Customer-level: invoices vs receipts → collection days and overdue ageing.
- No fuzzy or ML matching. What the passes don't match goes to Claude, which proposes matches through
  `fin reconcile match --propose <file.json>`, and the user confirms them (as with mappings).

**Done when:** the synthetic matching answer key is reproduced exactly.

### D6: Contract-period recast (handoff Module 3)
- Contracts entered from Claude readings of contract documents (a reading CSV, as for statement pages, with file +
  page). `read_by = 'claude'` until a second reading agrees.
- Recognition schedule per contract (straight-line over the service period, or milestones as entered), compared
  with revenue booked in the ledger by period. The difference by period is a proposed `recast` adjustment.

**Done when:** a synthetic contract billed up front recasts into the right periods and proposes the adjustment.

### D7: QoE outputs
- Adjusted EBITDA bridge (reported → each accepted adjustment → adjusted), EBITDA-to-cash walk, normalised working
  capital (monthly when monthly TBs exist).
- A diligence report template (`templates/report/diligence.qmd`) and a dashboard page, with the same narrative
  rules as today: describe, don't judge; numbers only as references.
- Gates G3–G5.

**Done when:** the synthetic engagement renders a diligence report whose every number traces to source rows.

### D8: Accountant query letter
- `fin query export <co>`: approved queries to Excel and Word, grouped by area, each with amounts, both sources and
  the period. Marks them `sent`.
- `fin query answer <co> <id> --text|--file`: records the reply (file archived like any source). Claude then
  proposes: close, re-reconcile, or an adjustment.

**Done when:** a synthetic round trip (draft → approve → export → answer → adjustment → G4) works end to end.

### D9: Customer, product, sector, pricing, retention analytics (separate module)
- Views on `register_lines`: customer concentration (top 1/5/10 share), cohort retention and net revenue retention
  by year, price/volume/mix for products with quantity and rate, sector mix (needs a customer → sector list: a
  `customers` table Claude fills, the user reviews).
- Dashboard page and a report section.

**Done when:** synthetic values match a precomputed answer key exactly.

## 8. What stays the same

- The statement layer: ingest, mapping, E401–E405, metrics, reports and dashboards behave as today.
- Lineage: every number traces to a file + sheet!cell/row or page. `fin audit` extends to query, adjustment and
  reconciliation ids.
- Conventions: run records, error codes, `db/conn.py`, one transaction per file, backups, synthetic fixtures only.
- Claude does the judgement: proposing ledger mappings, unmatched matches, query wording, adjustments, reading
  contracts. Code does only the repeatable parts.

## 9. Open questions for the handoff's author

1. **The five modules.** Modules 2, 3 and 4 are named above. What are 1 and 5? Is 1 data loading and 5 the
   customer analytics, or something else?
2. **Gates.** Which gates does the handoff define? Are §5's G1–G5 the right ones, and who approves: the user only?
3. **Tally format.** Excel exports (TB, day book, ledger vouchers) or XML? Which Tally version? Is a monthly TB
   available, or only year-end?
4. **Threshold.** Rs 1 lakh per item, per account per period, or aggregate? Is it the same for every
   reconciliation?
5. **Query workflow.** Who sends the queries, and how (email, a shared sheet)? Is a Word/Excel letter enough?
6. **Adjustment categories.** Which taxonomy (e.g. management, normalisation, run-rate, pro forma,
   reclassification)? Is the EBITDA definition the same as fin's (before exceptional items)?
7. **Bank statements.** Excel/CSV downloads, or PDFs? Which banks?
8. **Payroll.** Which system's export, and does it include employee-level data? If yes, it needs anonymising on load.
9. **Statement findings.** Should E401–E405 findings on audited statements also become queries automatically, or
   only when the user raises them?
