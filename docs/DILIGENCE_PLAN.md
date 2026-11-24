# Diligence extension — plan

Status, 2026-11-24: **phase D1 is built** (#43, #44, #45; build log: [DILIGENCE_BUILD.md](DILIGENCE_BUILD.md)). It
covers the engagement, accountant queries, EBITDA adjustments, the EBITDA bridge and EBITDA-to-cash walk on the
audited statements, and gates G1, G2 and G4. Phases D2–D9 are planned, revised to the diligence user's answers in §9.
One question is open: whether bank PDFs may go to LlamaParse (§9). Each phase moves into `PLAN.md` as it is built.

Purpose: a way to manage and analyse a company's financial documents efficiently, from the audited statements down
to the transactions behind them. The requirements started from a review of fin-analyst against a quality-of-earnings
(QoE) diligence handoff and were confirmed by the diligence user on 2026-11-24 (§9). The plan is organised by phase
(§7), not by the handoff's modules.

## 1. Goal

fin-analyst analyses **published statements** (P&L, balance sheet, cash flow). This plan extends it to the
**transactions behind them**, for a diligence engagement on one company:

| Need | Status | Phase |
|---|---|---|
| Audited statements, checks, metrics | **Built**: the statement layer | — |
| Proposed adjustments kept separate from reported numbers | **Built** (EBITDA effect); revenue and cost effects planned | D1; D4 |
| Accountant queries (Rs 1 lakh per line, drafted for approval) | **Built**, except grouping lines into issues and the export | D1; D4, D8 |
| Gates that stop the build for review | **Built**: G1, G2, G4; G3 planned | D1; D2, D4, D6 |
| Adjusted EBITDA, EBITDA bridge, EBITDA-to-cash walk | **Built** on the audited statements | D1 |
| Trial balance, Tally Ledger Vouchers, registers, bank, payroll, with source file + sheet!row lineage | Planned | D2, D3 |
| Names masked in everything fin prints; real values only in the accountant export | Planned | D2 on |
| Current-year stub from unaudited management data | Planned | D2 on |
| Reconciliation: register ↔ ledger ↔ accounts; pass-through revenue | Planned | D4 |
| Reconciliation: bank ↔ receipts ↔ invoices, with FX lines for USD | Planned | D5 |
| Service-period revenue recast from the sales register | Planned | D6 |
| Normalised working capital, diligence report and dashboard | Planned | D7 |
| Query export: marked-up workbook and draft email, or a brief per issue | Planned | D8 |
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
3. **Queries are drafted, never sent.** Claude drafts the wording, the user approves it, and the user sends it from
   their own email and marks it `sent`. fin never sends anything. (The export the user sends is D8.)
4. **Names stay on the machine (planned, D2 on).** The diligence user's rule is to anonymise before any third-party
   processing, and Claude Code sends what fin prints to Anthropic. fin stores the real values of the masked fields
   locally and prints a stable token in their place (§3, "Masking"). Real values appear only in the files
   `fin query export` writes for the user to send.

## 3. Concepts

- **Engagement**: one diligence job on one company: the annual periods in scope (e.g. FY24–FY26), an optional
  current-year stub, and the query threshold (default 100000.00 rupees, `config.toml [diligence] query_threshold`,
  or `--threshold`). It lives in the company database, beside the statement data. At most one is open per company.
- **Query threshold** (planned meaning, D4): Rs 1 lakh **per compared line**: one transaction, or one account when a
  trial balance is compared with the statements. A line at or above it goes into a query; a line below it stays
  listed in the reconciliation, with no query of its own. There is no per-account aggregate and no total-level
  cutoff.
- **Query**: a question for the company's accountant, with the amount, period, scope and sources (raised from a
  finding, or by hand). Status: `draft` → `approved` → `sent` → `answered` → `closed`, or `withdrawn` before closed.
  Planned (D4): a query is one **issue** that groups its difference lines (e.g. ten invoices missing from the
  ledger), with the count and value of each kind of difference.
- **Adjustment**: a proposed effect for a period and scope: a category (`config.toml [diligence]
  adjustment_categories`), optionally the dictionary account it relates to, a signed amount in rupees, a
  description and evidence (a query, a finding or a source reference; at least one). Status: `proposed` →
  `accepted` / `rejected`. Reported figures are never changed: adjusted EBITDA is reported EBITDA plus accepted
  adjustments, in a view.
  - Categories (planned default): `normalisation` (e.g. pass-through stripped, one-off items), `reclassification`
    (capitalisation, inventory movement, direct vs other expenses), `recast` (service-period mapping), `error`
    (true duplicates), `management` (e.g. director pay add-back). `run_rate` and `pro_forma` leave the default.
  - Planned (D4): an adjustment carries its effect on revenue and on costs as well as on EBITDA, because
    pass-through and reclassification change revenue and margins with little or no EBITDA effect.
- **Adjusted base EBITDA**: reported EBITDA (other income excluded, shown separately below EBITDA, as fin's EBITDA
  already does) plus accepted adjustments, which take out pass-through revenue and cost and exceptional or one-off
  items.
- **Pass-through** (planned, D4): per engagement, the user supplies a list of SI/reseller customers whose deals pass
  through at roughly zero margin. Their sales are pass-through. A sale's Purchase Id links it to the OEM purchase
  (populated: software or licence; blank: services); fin shows the link as information. Pass-through revenue is
  shown separately as project revenue and left out of growth figures. Valuing it at a lower multiple is the user's
  judgement; fin shows the split and no valuation.
- **Current-year stub** (planned, D2 on): an engagement may add a year-to-date period (e.g. April–September) built
  from unaudited management data: that period's trial balance and Ledger Vouchers, Excel bank statements and the
  registers, anchored to the last audited year. It is named by its fiscal year and end month, e.g. `FY27-YTD-SEP`,
  and labelled "unaudited management accounts" in every output. It is cross-checked against bank and registers and
  never annualised: base figures and growth use the audited years only.
- **Masking** (planned, D2 on): the masked fields are customer and vendor names, handler and submitted-by, employee
  name and code, and party names in Tally ledgers (ledgers under Sundry Debtors and Sundry Creditors). On load, fin
  stores each real value once in `mask_values` and writes a stable token in the data tables (`CUST-0042`,
  `VEND-0007`, `EMP-0113`, `USER-0003`). The same name gets the same token across files. In bank remarks, every
  known masked name is replaced by its token. fin never prints `mask_values`.
- **Gate**: a checkpoint with a checklist fin evaluates. Approving it records the hash of its inputs; when those
  inputs change, the gate reopens on its own and the user approves it again. Gates are approved in order.
- Planned (D2 on): **source ledgers** (transaction-level data, each row with its file sha256 and sheet!row) and
  **reconciliations** (a named comparison of two sources for a period; each line at or above the threshold goes into
  a query, those below are listed).

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
| `engagement` (changed) | adds stub: period name (e.g. `FY27-YTD-SEP`), start and end date | D2 |
| `source_files` | file_id, kind (`trial_balance`, `ledger_vouchers`, `registers`, `bank`, `payroll`, `contract`), file name, sha256, archive path, period covered (from the export header), basis (audited year or unaudited stub), profile, status | D2 |
| `mask_values` | field kind (customer, vendor, user, employee name, employee code), token, real value, first file_id. Never printed; read only by `fin query export` | D2 |
| `ledger_accounts` | Tally ledger (token for party ledgers), group, parent group, mapped Schedule III account (proposed by Claude, reviewed like label mappings), present in TB / in Ledger Vouchers | D2 |
| `trial_balance` | file_id, sheet, row, period, row type (group or ledger), group, ledger, closing debit, closing credit. No opening or movement columns: the export has none | D2 |
| `ledger_entries` | file_id, sheet, row, ledger (from the block header), date, particulars, voucher type, voucher no, debit, credit | D2 |
| `register_lines` | file_id, sheet, row, kind (sales/purchase), bill id, purchase id, customer id, customer (token), location, invoice no, contract start, contract end, invoice date, order type, product, service start, service end, quantity, rate, amount, currency, currency rate, amount in Rs (before GST), service type, handler and submitted-by (tokens) | D3 |
| `bank_lines` | file_id, sheet or page, row, bank, account, currency, date, remarks (names as tokens), reference, debit, credit, balance | D3 |
| `payroll_lines` | file_id, sheet, row, snapshot date, employee name and code (tokens), designation, department, vertical, sub-vertical, joining date, resign date, gross salary and CTC (annual, unit confirmed by the user). No month column | D3 |
| `recon_runs`, `recon_items` | each reconciliation and its lines (`matched`, `difference`, `left_only`, `right_only`, `fx`), each with both sources and its amount | D4, D5 |
| `query_items` | query_id, recon item id: the lines an issue groups; count and value per kind of difference come from it | D4 |
| `adjustments` (changed) | adds revenue effect and cost effect; the EBITDA effect (`amount`) must equal revenue effect minus cost effect; a reclassification between cost accounts names both (`account`, `from_account`) | D4 |
| `passthrough_customers` | engagement_id, customer token, source of the list, run | D4 |
| `contracts` | contract_id, customer (token), start, end, value, billing terms, source (file + page), read_by; optional evidence for the recast | D6 |

Planned views:

- `stub_figures` (D2): the stub's trial balance rolled up through `ledger_accounts` to Schedule III accounts,
  labelled unaudited management accounts. The audited years keep their statement figures.
- `revenue_split` (D4): revenue per period as core and pass-through (project revenue); growth on core revenue only.
- `adjusted_ebitda` (changed, D4): adds adjusted revenue, and margins on adjusted revenue.
- `nwc_year_end` (D7): working capital at each year-end (and the stub end) from TB closing balances mapped to
  working capital accounts.
- `nwc_monthly` (D7): monthly balances from Ledger Vouchers: the year's opening (TB closing minus the year's net
  movements) plus cumulative movements to each month end, for working capital ledgers whose movements tie to the TB
  (`gl_vs_tb`, D4). There are no monthly trial balances, so this is the only monthly view.

## 5. Gates

The diligence user reviews ingestion, reconciliation and recasting, and no query goes out without their approval.
The gates follow that.

| Gate | Checklist fin evaluates | Unlocks | Status |
|---|---|---|---|
| G1 Ingestion | Every annual period in scope has a loaded document with figures for it; no held documents; no mapping items waiting for review. Its inputs include the figures (`current_facts`), so a changed figure reopens it. D2 adds: a trial balance and Ledger Vouchers per period (and for the stub); every ledger mapped; every TB ledger present in Ledger Vouchers; each audited year's TB ties to its statements (each account difference at or above the threshold is in a query). The stub has no audited statement: its TB is checked against Ledger Vouchers only, and it is marked unaudited. D3 adds: payroll unit confirmed; every bank parse ties | approving G2 | **Built** (statement checks) as "Data" |
| G2 Reconciliation | No query in draft. A new or reworded query reopens it; sending, answering, closing or withdrawing one does not. D4–D5 add: every required reconciliation ran; every line at or above the threshold is in a query (`fx` lines are listed, not queried) | `fin adjust accept\|reject` | **Built** as "Queries" |
| G3 Recast | Every sales line at or above the threshold has service start and end dates, or is in a query; the recast ran for every period; each period's recast difference has a proposed or decided `recast` adjustment. Contracts are optional evidence | approving G4 | Planned (D6) |
| G4 Adjustments | No proposed adjustment; every accepted one has evidence | `fin engagement close` | **Built** |

There is no report gate: the planned G5 is dropped. The diligence report (D7) shows the gate states it was rendered
with. Renaming G1 and G2 to "Ingestion" and "Reconciliation" is a small code change with D2 and D4.

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
| `fin engagement open <co> --periods ... [--stub FY27-YTD-SEP]` | D2 |
| `fin ingest <co> <file> --kind trial_balance\|ledger_vouchers\|registers\|bank\|payroll [--profile]` | D2, D3 |
| `fin map ledgers <co>` (Tally ledger → account, through `fin review`) | D2 |
| `fin payroll confirm-unit <co> <file> --annual\|--monthly` (the user says which) | D3 |
| `fin engagement passthrough <co> --customers <file>` (the user's SI/reseller list; fin prints the tokens it matched) | D4 |
| `fin reconcile <co> [name] [--period]`, `fin reconcile match --propose <file.json>` | D4, D5 |
| `fin query raise <co> --items <recon item ids>` (one issue, many lines) | D4 |
| `fin adjust propose ... --revenue N --cost N` | D4 |
| `fin report <co> --diligence` | D7 |
| `fin query export <co> [--brief]` | D8 |

New error codes start at E807 (e.g. an unknown column profile, a file period outside the engagement, a trial balance
with group rows only, `fin sql` reading `mask_values`).

## 7. Phases

One PR per phase, each small enough to review in one sitting, with tests on synthetic fixtures only. Fixtures use the
column headers received on 2026-11-24 (§9) and made-up names.

### D0: Confirm requirements (no code)
Two rounds of answers were received on 2026-11-24 (§9), with the column headers for every Excel format. One
question is open: whether bank PDFs may go to LlamaParse. No sample PDFs: the diligence user tests bank PDFs on their
own files once D3 is built.

**Done when:** the open question in §9 is answered. It blocks only the LlamaParse path in D3.

### D1: Foundation — done (#43, #44, #45)
Engagement, accountant queries, EBITDA adjustments with evidence, `diligence_events`; the views `adjusted_ebitda`,
`ebitda_bridge`, `ebitda_to_cash` and `fin engagement bridge`; gates G1, G2, G4 with `fin gate`, E801/E802 refusals
and the approval hook; `fin status` summary; `CLAUDE.md` "Diligence" rules and `/diligence`.

Adjustments are effects on EBITDA, so there are no `adjusted_facts` / `adjusted_metrics` views (every metric on
adjusted facts). D4 adds revenue and cost effects, which is enough for adjusted revenue and margins.

### D2: Trial balance, Tally Ledger Vouchers, masking, stub
- A tabular loader (`fin/extract/ledger.py`) driven by column **profiles** (`templates/profiles/*.toml`: header
  names → fields, date format, debit/credit or signed amount, unit, which fields are masked). An unknown layout
  fails with a new error code and lists the headers it found, so Claude can write a profile.
- **Trial balance** (Tally Excel, full year or stub): Particulars, closing debit and credit, under export-period
  header lines. The period is read from the header and must be a period of the engagement. Group rows are told
  apart from ledger rows by the export's layout (the profile says how), and each group must equal the sum of its
  ledgers or the load fails. A TB with group rows only fails with a message asking for the Detailed export. fin
  reports the ledgers present.
- **Ledger Vouchers**: one export of all ledgers, in ledger-name blocks (Date, Particulars, Vch Type, Vch No.,
  Debit, Credit), balance-sheet ledgers included. Each row takes its ledger from the block header. fin reports the
  ledgers present and lists TB ledgers missing from it, so Claude asks the user for the export with all ledgers.
- **Masking** (§3): party ledgers are tokenised on load; `mask_values` holds the real names; `fin sql` refuses to
  read it.
- TB ledgers map to Schedule III accounts (`ledger_accounts`), proposed by Claude and confirmed in review.
- Built-in reconciliation `tb_vs_statements`: TB closing balances rolled up by account vs the audited statement
  facts, per audited period; one line per account. The stub has no statement: `stub_figures` gives its figures,
  labelled unaudited.
- `fin engagement open --stub`; G1 gains the trial balance checks and is renamed "Ingestion" (§5).

**Done when:** a synthetic TB and Ledger Vouchers load with sheet!row lineage and masked party names; a group-only TB
is refused; a planted account difference at or above the threshold becomes a draft query, and one below it is listed;
a synthetic stub loads and is labelled unaudited.

### D3: Registers, bank, payroll
- **Registers**: one workbook from the billing system, a sales sheet and a purchase sheet, headers as in §9
  (`register_lines`, §4). Amount in Rs is before GST; there are no GST or tax columns. Customer, vendor, handler and
  submitted-by are masked.
- **Bank**: Excel profiles for ICICI, SBI and Axis (INR and USD accounts). Remarks keep their text with known names
  replaced by tokens.
- **PDF bank statements: the chosen PDF parser** (owner decision, 2026-11-24). fin reads a bank PDF's tables with
  the parser in `config.toml` (`[pdf] parser`: Docling, LiteParse or LlamaParse; the setting is built in a separate
  PR) and maps them to `bank_lines` with the same profiles as the Excel statements, keeping file + page lineage.
  LiteParse is the default for bank PDFs: nothing leaves the machine.
  - Bank PDFs go to LlamaParse only if the user confirms it (open question, §9); until then they use a local parser.
    LlamaParse's API key comes from the environment (`LLAMA_CLOUD_API_KEY`), never from the repo or the data folder.
    A missing key or a failed call raises an error with the run_id; nothing is loaded.
  - A parse is saved in the data folder with the file's sha256, so ingesting the same file again does not parse it
    again, and every loaded row can be traced to the parse it came from.
  - A parse whose rows do not tie (opening balance + credits − debits ≠ closing balance) is a finding, not a load.
  - Tests use recorded parser output for a synthetic PDF; CI never calls the LlamaParse API.
- **Payroll**: the Sparsh snapshot, one row per employee; gross salary and CTC are annual. fin asks the user to
  confirm the unit (`fin payroll confirm-unit`) and warns when the amounts look monthly. Employee name and code are
  masked. The separate monthly salary workbooks are out of scope for now: a later optional profile.
- Duplicate and overlap checks: the same invoice number twice, overlapping bank periods, a gap in bank balances
  (closing ≠ next opening). These are findings, then queries.

**Done when:** each synthetic file loads with names masked; the planted duplicate invoice, bank balance gap and a
parse that does not tie are found; a payroll file whose amounts look monthly is warned about.

### D4: Register ↔ ledger ↔ accounts reconciliation; pass-through; adjustment effects
- `sales_register_vs_gl`, `purchase_register_vs_gl` (monthly, then by party, then by invoice), `gl_vs_tb` (Ledger
  Vouchers movements vs TB closing), `payroll_vs_gl` (annual). Deterministic SQL, one function per reconciliation
  in `fin/reconcile/`. Registers compare on Amount in Rs (before GST).
- USD invoices: the register's Amount in Rs (Amount × Currency Rate) is compared with the books; the difference
  from the ledger's conversion rate is an `fx` line, shown as FX gain or loss, never plugged.
- Threshold per line (§3). Claude groups the lines at or above it into issues (`fin query raise --items`); the user
  approves them. G2 gains the reconciliation checks and is renamed "Reconciliation" (§5).
- Pass-through: `fin engagement passthrough` records the user's customer list; `revenue_split` shows core and
  pass-through (project) revenue, with the Purchase Id link to the OEM purchase; growth uses core revenue. Claude
  proposes the `normalisation` adjustment that strips pass-through revenue and its cost.
- Adjustments gain revenue and cost effects (§4); `adjusted_ebitda` gains adjusted revenue and margins on it. The
  default `adjustment_categories` drops `run_rate` and `pro_forma` (a small config change).
- The stub is cross-checked: its TB vs Ledger Vouchers and vs the registers, here; vs bank in D5.

**Done when:** every planted difference in the synthetic set is found, with both sources, and nothing else is; a
planted USD rate difference shows as an `fx` line; a pass-through customer's revenue is split out and the
reclassification adjustment changes margins with zero EBITDA effect.

### D5: Bank ↔ receipts ↔ invoices
- Matching in fixed passes: exact reference, then amount + party + date window (config), then amount + date
  window. Each match records the pass that made it. Unmatched items become `left_only` / `right_only`.
- Axis USD receipts match on customer, USD amount and a date window; the booked rupee value is compared, and a
  difference is an `fx` line (FX gain or loss), never plugged.
- Customer-level: invoices vs receipts → collection days and overdue ageing.
- No fuzzy or ML matching. What the passes don't match goes to Claude, which proposes matches through
  `fin reconcile match --propose <file.json>`, and the user confirms them (as with mappings).

**Done when:** the synthetic matching answer key, including a USD receipt with an FX difference, is reproduced exactly.

### D6: Service-period recast
- The sales register's service start and end dates drive the recast: each line's amount in Rs is spread
  straight-line over its service period, by month, and compared with revenue booked by period. The difference by
  period is a proposed `recast` adjustment (revenue effect and EBITDA effect).
- A line without service dates is recognised on its invoice date and listed; at or above the threshold it goes into
  a query.
- Contract documents are optional evidence: Claude can read one (a reading CSV, as for statement pages, with file +
  page) to support a line or an answer. They are not the source of the schedule.
- Gate G3 (§5).

**Done when:** a synthetic line billed up front for a 12-month service recasts into the right periods and proposes
the adjustment; a line without service dates is listed.

### D7: QoE outputs
- Normalised working capital from year-end trial balances (`nwc_year_end`), and monthly from Ledger Vouchers
  movements (`nwc_monthly`, §4). The EBITDA bridge and EBITDA-to-cash walk are built (D1).
- A diligence report template (`templates/report/diligence.qmd`) and a dashboard page, with the same narrative
  rules as the health report: describe, don't judge; numbers only as references. Adjusted base EBITDA, other income
  below it, pass-through shown as project revenue, the stub labelled unaudited management accounts, and the gate
  states it was rendered with. Names appear as tokens.

**Done when:** the synthetic engagement renders a diligence report whose every number traces to source rows.

### D8: Query export
- `fin query export <co>`: for the approved queries, a marked-up Excel workbook (each issue's difference lines
  highlighted in the source layout, with the count and value per difference) and a draft summary email as text.
  `--brief`: a 1–2 page Word brief per issue instead. No Word query letter, no shared sheet.
- Claude drafts with tokens; the export writes the real values from `mask_values` into the files, in the data
  folder. fin prints only the file paths. The user sends from their own email and then runs `fin query sent`.
- `fin query answer <co> <id> --file`: records a reply file (archived like any source). Claude then proposes: close,
  re-reconcile, or an adjustment.

**Done when:** a synthetic round trip (draft → approve → export → answer → adjustment → G4) works end to end, and the
export holds real names while fin's output holds only tokens.

### D9: Customer, product, sector, pricing, retention analytics
- Views on `register_lines`: customer concentration (top 1/5/10 share), cohort retention and net revenue retention
  by year, price/volume/mix for products with quantity and rate, sector mix (needs a customer → sector list: a
  `customers` table Claude fills, the user reviews). Core and pass-through revenue shown separately. Customers appear
  as tokens.
- Dashboard page and a report section.

**Done when:** synthetic values match a precomputed answer key exactly.

## 8. Shared with the statement layer

- Diligence reads the statement layer (ingest, mapping, E401–E405, metrics) and never writes to it.
- Lineage: every number traces to a file + sheet!cell/row or page. `fin audit` covers query, adjustment and
  reconciliation ids too (planned).
- Conventions: run records, error codes, `db/conn.py`, one transaction per change, synthetic fixtures only.
- Claude does the judgement: proposing ledger mappings, unmatched matches, query wording, adjustments, reading
  contracts. Code does only the repeatable parts. Claude sees masked names only (§2, rule 4).

## 9. Questions for the diligence user

**Answered on 2026-11-24** (the diligence user's reviewed answers; §1–§7 above are revised to match them):

- Tally: Excel exports, not XML. Trial balances are full-year only (Particulars, closing debit and credit, under
  export-period header lines); the period is read from the export header. Ledger Vouchers: Date, Particulars, Vch
  Type, Vch No., Debit, Credit, grouped under ledger account-name blocks. Tally version unknown.
- Bank: ICICI (primary), SBI and Axis (INR and USD accounts). Full-year statements as PDF, current-year periods as
  Excel. PDFs go through the chosen PDF parser (D3).
- Payroll: Sparsh, one row per employee (name, code, designation, department, vertical, sub-vertical, joining and
  resign dates, gross salary, CTC). Anonymise before any third-party processing: mask name and code at minimum.
- Sales and purchase registers come in one workbook, from a billing system, with contract and service start and end
  dates, currency and rupee amounts. Customer and vendor names, and handler and submitted-by columns, are masked.
- Threshold: Rs 1 lakh per transaction is the only query cutoff. Smaller differences stay listed without an
  individual query; nothing per account or aggregate.
- Adjustment categories: normalisation (pass-through stripped at zero margin), reclassification (capitalisation,
  inventory movement, direct vs other expenses), recast (service-period mapping), error (true duplicates),
  management (director pay add-back). No run_rate or pro_forma. EBITDA is adjusted base EBITDA, excluding
  pass-through revenue and cost and exceptional or one-off items.
- Queries: the diligence user sends them from their own email after reviewing a draft: a summary email with a
  marked-up Excel (lines of difference highlighted, count and value per difference), or a 1–2 page brief per issue.
  No shared sheet, no Word query letter.
- Gates: ingestion, reconciliation and recasting each gate on the diligence user's review; no query goes out
  without their approval.

**Answered on 2026-11-24, second round** (engagement-specific variation noted; the diligence user is open to
further targeted questions):

1. **Pass-through** is identified by customer and deal, not by Order Type or Service Type: an engagement-specific
   list of SI/reseller customers whose deals pass through at roughly zero margin. The tool takes that customer list
   as an input per engagement. Purchase Id links a sale to the OEM purchase (Broadcom/Symantec in the current
   engagement); rule of thumb: a Purchase Id means software or licence, a blank means services. Pass-through is
   shown as project revenue, left out of growth figures and valued at a lower multiple.
2. **Other income** is excluded from EBITDA and shown separately below it (as fin's EBITDA already does).
3. **Current-year stub:** an engagement can include a year-to-date stub (April–September in the current case),
   from unaudited management data: the period's Tally TB and Ledger Vouchers, Excel bank statements and the
   registers, anchored to the last audited year. It is labelled as unaudited management accounts, cross-checked
   against bank and registers, and never annualised into base figures.
4. **Payroll:** the Sparsh export is a snapshot, one row per employee; Gross Salary and CTC are annual. Separate
   monthly salary workbooks exist. The tool asks the user to confirm the unit and warns when amounts look monthly.
5. **Register amounts:** Amount in Rs is before GST (the purchase register tied to audited purchases within about
   1% with no GST adjustment). For USD invoices, the register's Amount in Rs (Amount × Currency Rate) is used to
   reconcile with the books; a difference from the ledger's conversion rate is shown as an FX line. Axis USD
   receipts match on customer, USD amount and a date window; the booked rupee value is compared and a difference
   is reported as FX gain or loss, never plugged.
6. **Tally:** Ledger Vouchers is one large export of all ledgers, in ledger-name blocks, balance-sheet ledgers
   included. The TB has group lines. The tool tells group rows from ledger rows, asks for Detailed mode when it
   finds only groups, asks for all ledgers, and reports the ledgers actually present.

**Open:**

1. **LlamaParse.** Bank PDFs carry names. If LlamaParse (a cloud service) is the chosen PDF parser, may bank PDFs
   go to it, or must they stay on a local parser (Docling or LiteParse)?

Statement findings (E401–E405) become queries only when the user raises them, as built.
