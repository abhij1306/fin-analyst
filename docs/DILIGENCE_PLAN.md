# Diligence extension — plan

Status, 2026-11-09: **phase D1 is built** (#43, #44, #45; build log: [DILIGENCE_BUILD.md](DILIGENCE_BUILD.md)). It
covers the engagement, accountant queries, EBITDA adjustments, the EBITDA bridge and EBITDA-to-cash walk on the
audited statements, and gates G1, G2 and G4. Phases D2–D9 need the transaction data and wait on the open questions
in §9. Each phase moves into `PLAN.md` as it is built.

Source: a review of fin-analyst against a quality-of-earnings (QoE) diligence handoff. We have the review, not the
handoff itself, so every requirement below is inferred from it.

## 1. Goal

fin-analyst analyses **published statements** (P&L, balance sheet, cash flow). This plan extends it to the
**transactions behind them**, for a diligence engagement on one company:

| Need | Status | Phase |
|---|---|---|
| Audited statements, checks, metrics | **Built**: the statement layer | — |
| Proposed adjustments kept separate from reported numbers | **Built** (EBITDA adjustments) | D1 |
| Accountant queries (Rs 1 lakh threshold, drafted for approval) | **Built**, except the letter export | D1; export D8 |
| Gates that stop the build for review | **Built**: G1, G2, G4 | D1; G3, G5 with D6, D7 |
| Adjusted EBITDA, EBITDA bridge, EBITDA-to-cash walk | **Built** on the audited statements | D1 |
| Sales and purchase registers, Tally ledgers, trial balance, bank, payroll | Planned, with source file + sheet!row lineage | D2, D3 |
| Reconciliation: register ↔ ledger ↔ accounts | Planned | D4 |
| Reconciliation: bank ↔ receipts ↔ invoices | Planned | D5 |
| Contract-period revenue recast from contract evidence | Planned | D6 |
| Normalised working capital, diligence report and dashboard | Planned | D7 |
| Customer, product, sector, pricing, retention analytics | Planned | D9 |

## 2. Rules for diligence data

These apply to diligence data only; the statement layer keeps its rules (findings: save as is, or have Claude fix).
They are in `CLAUDE.md`, "Diligence".

1. **Differences are queried, never fixed.** A difference in what the company filed or booked stays as found. It
   is closed only by an accountant's answer (`fin query answer`, `close`) or by an adjustment the user accepts. No
   command edits company data to make a reconciliation tie.
   - Fixing **fin's own** errors (a wrong mapping, a misread column) is still allowed and audited.
   - Statement findings (E401–E405) can be raised as queries (`fin query raise --finding <id>`); the finding is not
     changed.
2. **Gates stop the build.** Commands behind a gate refuse (E801, exit 3) until the user approves it. Claude
   prepares a gate and shows `fin gate status`, but never approves one: a PreToolUse hook
   (`.claude/hooks/gate_guard.py`) makes Claude Code ask the user before any command that looks like a gate
   approval (the word `gate` followed by `approve`, or a name such as `gate_approvals` or `gates.approve`, after
   removing quotes and line breaks; when in doubt it asks). The diligence tables change only through `fin
   engagement|query|adjust|gate`: `fin sql --write` refuses to write them (E806), so it cannot write gate
   approvals. All other commands run without prompts and are recorded in the audit log.
3. **Queries are drafted, never sent.** Claude drafts the wording, the user approves it, and the user sends it and
   marks it `sent`. fin never sends anything. (The Excel/Word letter export is D8.)

## 3. Concepts

- **Engagement**: one diligence job on one company: the annual periods in scope (e.g. FY24–FY26) and the query
  threshold (default 100000.00 rupees, `config.toml [diligence] query_threshold`, or `--threshold`). It lives in the
  company database, beside the statement data. At most one is open per company.
- **Query**: a question for the company's accountant, with the amount, period, scope and sources (raised from a
  finding, or by hand). Status: `draft` → `approved` → `sent` → `answered` → `closed`, or `withdrawn` before closed.
- **Adjustment**: a proposed effect on EBITDA for a period and scope: a category (`config.toml [diligence]
  adjustment_categories`), optionally the dictionary account it relates to, a signed amount in rupees, a
  description and evidence (a query, a finding or a source reference; at least one). Status: `proposed` →
  `accepted` / `rejected`. Reported figures are never changed: adjusted EBITDA is reported EBITDA plus accepted
  adjustments, in a view.
- **Gate**: a checkpoint with a checklist fin evaluates. Approving it records the hash of its inputs; when those
  inputs change, the gate reopens on its own and the user approves it again. Gates are approved in order.
- Planned (D2 on): **source ledgers** (transaction-level data, each row with its file sha256 and sheet!row) and
  **reconciliations** (a named comparison of two sources for a period; each difference above the threshold becomes
  a draft query, those below are listed and summed).

## 4. Data model (company database)

Same conventions as `company.sql`: DECIMAL(20,2) rupees, no foreign keys, ids as links, a run_id on every change.
Greenfield: schema changes edit the schema files; there are no migrations.

**Built** (`src/fin/db/schema/diligence.sql`, `diligence_views.sql`):

| Table / view | Holds |
|---|---|
| `engagement` | engagement_id, periods, query_threshold, status (open/closed), opened and closed run and time |
| `accountant_queries` | query_id, engagement_id, origin (finding/manual), finding_id, period, scope, question, amount, sources, status, answer, answer_source, note (withdrawal reason), created and status-change run and time |
| `adjustments` | adj_id, engagement_id, period, scope, category, account, amount (effect on EBITDA), description, evidence (query_id, finding_id, source_ref), status, reason, created and status-change run and time |
| `gate_approvals` | approval_id, engagement_id, gate, input_hash, input_hashes (per input, to say what changed), checklist, approved run and time; append-only |
| view `adjusted_ebitda` | per period and scope of the latest engagement: revenue, reported EBITDA, accepted adjustments, adjusted EBITDA, both margins, proposed adjustments (shown, not applied), counts |
| view `ebitda_bridge` | reported EBITDA, each accepted adjustment, adjusted EBITDA, in step order |
| view `ebitda_to_cash` | EBITDA → non-cash and other → working capital change → taxes paid → other operating = CFO exactly; capex, free cash flow, cash conversion (reported and adjusted); a missing input makes its lines NULL |

Audit (`shared/audit.duckdb`, append-only): `diligence_events` records every engagement, query and adjustment change
and every gate approval.

**Planned:**

| Table | Holds | Phase |
|---|---|---|
| `source_files` | file_id, kind (`trial_balance`, `ledger`, `sales_register`, `purchase_register`, `bank`, `payroll`, `contract`), file name, sha256, archive path, period covered, profile, status | D2 |
| `ledger_accounts` | Tally ledger, group, parent group, mapped Schedule III account (proposed by Claude, reviewed like label mappings) | D2 |
| `trial_balance` | file_id, sheet, row, period_end, ledger, opening, debit, credit, closing | D2 |
| `ledger_entries` | file_id, sheet, row, date, voucher type and no, ledger, counter-ledger, party, debit, credit, narration | D2 |
| `register_lines` | file_id, sheet, row, kind, date, invoice no, party, GSTIN, item, quantity, rate, taxable value, tax, total | D3 |
| `bank_lines` | file_id, sheet, row, account, date, description, reference, debit, credit, balance | D3 |
| `payroll_lines` | file_id, sheet, row, month, employee id (as given), department, gross, deductions, net, employer costs | D3 |
| `recon_runs`, `recon_items` | each reconciliation and its matched items and differences (`matched`, `difference`, `left_only`, `right_only`) | D4, D5 |
| `contracts` | contract_id, customer, start, end, value, billing terms, recognition basis, source (file + page), read_by | D6 |

Planned views: `nwc_monthly` (normalised working capital from monthly trial balances, D7). Adjustments may also link
to a reconciliation or a contract once those exist.

## 5. Gates

| Gate | Checklist fin evaluates | Unlocks | Status |
|---|---|---|---|
| G1 Data | Every annual period in scope has a loaded document with figures for it; no held documents; no mapping items waiting for review. Its inputs include the figures (`current_facts`), so a changed figure reopens it. D2 adds: a trial balance per period, every ledger mapped, TB ties to the statements (differences become queries) | approving G2 | **Built** (statement checks) |
| G2 Queries | No query in draft. A new or reworded query reopens it; sending, answering, closing or withdrawing one does not. D4–D5 add: every required reconciliation ran, every difference above the threshold has a query | `fin adjust accept\|reject` | **Built** |
| G3 Recast | Every contract above the threshold has a schedule; recast differences have proposed adjustments | adjustments | Planned (D6) |
| G4 Adjustments | No proposed adjustment; every accepted one has evidence | `fin engagement close` | **Built** |
| G5 Report | The report is rendered from data matching the G4 approval | close | Planned (D7) |

States: `approved` (inputs unchanged since its latest approval, and every earlier gate approved), `reopened` (inputs
changed, with what changed, or an earlier gate not approved), `open` (never approved).

## 6. Commands

**Built:**

| CLI | Claude Code | Who |
|---|---|---|
| `fin engagement open <co> --periods FY24,FY25,FY26 [--threshold N]`, `status`, `close` | `/diligence` | Either; `close` needs G4 |
| `fin engagement bridge <co> [--period] [--scope] [--json]` | `/diligence` | Either |
| `fin query list\|raise\|edit\|approve\|sent\|answer\|close\|withdraw <co>` | `/diligence` | Claude drafts; approve, sent, close when the user says |
| `fin adjust propose\|list\|accept\|reject <co>` | `/diligence` | Claude proposes; accept/reject (need G2) when the user says |
| `fin gate status\|approve <co> [gate]` | `/diligence` | `approve`: the user only (the hook asks) |

`fin status` shows an open engagement's queries, proposed adjustments and gate states.

Error codes (README "Error codes"): E801 a gate the command needs is not approved; E802 a gate cannot be approved
(checklist fails, or an earlier gate is not approved); E803 no open engagement, or one already open; E804 an
adjustment is not valid (no evidence, category not in config, period not in the engagement, unknown account); E805
an unknown query, adjustment or finding id, or a status change not allowed; E806 `fin sql --write` given a statement
that writes to a diligence table.

**Planned:**

| CLI | Phase |
|---|---|
| `fin ingest <co> <file> --kind trial_balance\|ledger\|sales_register\|purchase_register\|bank\|payroll [--profile]` | D2, D3 |
| `fin map ledgers <co>` (Tally ledger → account, through `fin review`) | D2 |
| `fin reconcile <co> [name] [--period]`, `fin reconcile match --propose <file.json>` | D4, D5 |
| `fin query export <co>` (the letter) | D8 |
| `fin report <co> --diligence` | D7 |

New error codes start at E807 (e.g. an unknown column profile, a file period outside the engagement).

## 7. Phases

One PR per phase, each small enough to review in one sitting, with tests on synthetic fixtures only.

### D0: Confirm requirements (no code)
Answer §9 with the handoff's author. Get one anonymised sample of each export format (Tally TB, day book or ledger,
sales register, bank statement, payroll), or its column headers. Build the synthetic fixtures from them.

**Done when:** §9 is answered and fixture column layouts are agreed.

### D1: Foundation — done (#43, #44, #45)
Engagement, accountant queries, EBITDA adjustments with evidence, `diligence_events`; the views `adjusted_ebitda`,
`ebitda_bridge`, `ebitda_to_cash` and `fin engagement bridge`; gates G1, G2, G4 with `fin gate`, E801/E802 refusals
and the approval hook; `fin status` summary; `CLAUDE.md` "Diligence" rules and `/diligence`.

Adjustments are effects on EBITDA, so there are no `adjusted_facts` / `adjusted_metrics` views (every metric on
adjusted facts). Add them when a metric beyond EBITDA, its margin and cash conversion needs adjusting.

### D2: Trial balance and Tally ledgers
- A tabular loader (`fin/extract/ledger.py`) driven by column **profiles** (`templates/profiles/*.toml`: header
  names → fields, date format, debit/credit or signed amount, unit). Ship profiles for Tally's standard Excel
  exports. An unknown layout fails with a new error code and lists the headers it found, so Claude can write a
  profile.
- TB ledgers map to Schedule III accounts (`ledger_accounts`), proposed by Claude and confirmed in review.
- Built-in reconciliation `tb_vs_statements`: TB closing balances rolled up by account vs the audited statement
  facts, per period.
- G1 gains the trial balance checks (§5).

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
- Differences above the threshold → draft queries. G2 gains the reconciliation checks (§5).

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
- Gate G3.

**Done when:** a synthetic contract billed up front recasts into the right periods and proposes the adjustment.

### D7: QoE outputs
- Normalised working capital (monthly when monthly TBs exist). The EBITDA bridge and EBITDA-to-cash walk are built
  (D1).
- A diligence report template (`templates/report/diligence.qmd`) and a dashboard page, with the same narrative
  rules as the health report: describe, don't judge; numbers only as references.
- Gate G5.

**Done when:** the synthetic engagement renders a diligence report whose every number traces to source rows.

### D8: Accountant query letter
- `fin query export <co>`: approved queries to Excel and Word, grouped by area, each with amounts, both sources and
  the period.
- `fin query answer <co> <id> --file`: records a reply file (archived like any source). Claude then proposes: close,
  re-reconcile, or an adjustment.

**Done when:** a synthetic round trip (draft → approve → export → answer → adjustment → G4) works end to end.

### D9: Customer, product, sector, pricing, retention analytics (separate module)
- Views on `register_lines`: customer concentration (top 1/5/10 share), cohort retention and net revenue retention
  by year, price/volume/mix for products with quantity and rate, sector mix (needs a customer → sector list: a
  `customers` table Claude fills, the user reviews).
- Dashboard page and a report section.

**Done when:** synthetic values match a precomputed answer key exactly.

## 8. Shared with the statement layer

- Diligence reads the statement layer (ingest, mapping, E401–E405, metrics) and never writes to it.
- Lineage: every number traces to a file + sheet!cell/row or page. `fin audit` covers query, adjustment and
  reconciliation ids too (planned).
- Conventions: run records, error codes, `db/conn.py`, one transaction per change, synthetic fixtures only.
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
6. **Adjustment categories.** Which taxonomy? Built with: management, normalisation, reclassification, run_rate,
   pro_forma, recast, error (`config.toml`). Is the EBITDA definition the same as fin's (before exceptional items)?
7. **Bank statements.** Excel/CSV downloads, or PDFs? Which banks?
8. **Payroll.** Which system's export, and does it include employee-level data? If yes, it needs anonymising on load.
9. **Statement findings.** Should E401–E405 findings on audited statements also become queries automatically, or
   only when the user raises them?
