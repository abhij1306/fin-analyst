# Diligence extension — plan

Status, 2026-11-25: **D1 to D9 are built** (engagement, queries, adjustments, bridge and cash walk, gates G1 to G4, source files and masking, reconciliations, recast, working capital, analytics, the diligence report). The sections below keep the plan and its reasoning; the last section is the reference for what was built and how to run it. Each phase's design choices are in [decisions/](decisions/). The golden evaluation (`fin eval`) scores each phase's outputs; each phase adds its own check kinds ([development.md](development.md), "Eval checks").

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
| Proposed adjustments kept separate from reported numbers | **Built**, with revenue and cost effects | D1; D4 |
| Accountant queries (Rs 1 lakh per line, drafted for approval) | **Built**, with issues and the export | D1; D4, D8 |
| Gates that stop the build for review | **Built**: G1, G2, G3, G4 | D1; D2, D4, D6 |
| Adjusted EBITDA, EBITDA bridge, EBITDA-to-cash walk | **Built** on the audited statements | D1 |
| Trial balance, Tally Ledger Vouchers, registers, bank, payroll, with source file + sheet!row lineage | **Built**: all five (D2: trial balance, Ledger Vouchers; D3: registers, bank Excel and PDF, payroll) | D2, D3 |
| Names masked in everything fin prints; real values only in the accountant export | **Built** for Tally party ledgers and the other sources; the export is built | D2 on; D8 |
| Current-year stub from unaudited management data | **Built** (`stub_figures`, `bank_vs_tb`, the register and ledger reconciliations) | D2 on |
| Pass-through revenue split out of core revenue and growth | **Built** | D4 |
| Reconciliation: register ↔ ledger ↔ accounts | **Built** | D4 |
| Reconciliation: bank ↔ receipts ↔ invoices, with FX lines for USD | **Built**: matching passes, `fx` lines, collections view, Claude's proposals; the stub's bank cross-check | D5 |

| Service-period revenue recast from the sales register | **Built** | D6 |
| Normalised working capital, diligence report and dashboard | **Built** (choices: `docs/decisions/d7-qoe.md`) | D7 |
| Query export: marked-up workbook and draft email, or a brief per issue | **Built** (`fin query export`, `answer --file`; choices: `docs/decisions/d8-query-export.md`) | D8 |
| Customer, product, sector, pricing, retention analytics | **Built** | D9 |

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
4. **Names stay on the machine (built for Tally ledgers, D2; the rest as each source is added).** The diligence user's rule is to anonymise before any third-party
   processing, and Claude Code sends what fin prints to Anthropic. fin stores the real values of the masked fields
   locally and prints a stable token in their place (§3, "Masking"). Real values appear only in the files
   `fin query export` writes for the user to send. The PDF parser is the user's choice: a cloud parser
   (LlamaParse) sees the pages as they are (D3).

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
  Built (D4): a query is one **issue** that groups its difference lines (e.g. ten invoices missing from the
  ledger), with the count and value of each kind of difference.
- **Adjustment**: a proposed effect for a period and scope: a category (`config.toml [diligence]
  adjustment_categories`), optionally the dictionary account it relates to, a signed amount in rupees, a
  description and evidence (a query, a finding or a source reference; at least one). Status: `proposed` →
  `accepted` / `rejected`. Reported figures are never changed: adjusted EBITDA is reported EBITDA plus accepted
  adjustments, in a view.
  - Categories (planned default): `normalisation` (e.g. pass-through stripped, one-off items), `reclassification`
    (capitalisation, inventory movement, direct vs other expenses), `recast` (service-period mapping), `error`
    (true duplicates), `management` (e.g. director pay add-back). `run_rate` and `pro_forma` leave the default.
  - **Built** (D4): an adjustment carries its effect on revenue and on costs as well as on EBITDA, because
    pass-through and reclassification change revenue and margins with little or no EBITDA effect.
- **Adjusted base EBITDA**: reported EBITDA (other income excluded, shown separately below EBITDA, as fin's EBITDA
  already does) plus accepted adjustments, which take out pass-through revenue and cost and exceptional or one-off
  items.
- **Pass-through** (**built**, D4): per engagement, the user supplies a list of SI/reseller customers whose deals pass
  through at roughly zero margin. Their sales are pass-through. A sale's Purchase Id links it to the OEM purchase
  (populated: software or licence; blank: services); fin shows the link as information. Pass-through revenue is
  shown separately as project revenue and left out of growth figures. Valuing it at a lower multiple is the user's
  judgement; fin shows the split and no valuation.
- **Current-year stub** (planned, D2 on): an engagement may add a year-to-date period (e.g. April–September) built
  from unaudited management data: that period's trial balance and Ledger Vouchers, Excel bank statements and the
  registers, anchored to the last audited year. It is named by its fiscal year and end month, e.g. `FY27-YTD-SEP`,
  and labelled "unaudited management accounts" in every output. It is cross-checked against bank and registers and
  never annualised: base figures and growth use the audited years only.
- **Masking** (built in D2 for Tally party ledgers; `fin/diligence/mask.py`): the masked fields are customer and vendor names, handler and submitted-by, employee
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
| `engagement` (changed, **built**) | adds stub: period name (e.g. `FY27-YTD-SEP`), start and end date | D2 |
| `source_files` (**built**) | file_id, kind (`trial_balance`, `ledger_vouchers`, `registers`, `bank`, `payroll`, `contract`), file name, sha256, archive path, period covered (from the export header), basis (audited year or unaudited stub), profile, status | D2 |
| `mask_values` (**built**) | field kind (customer, vendor, user, employee name, employee code, or ledger: a Ledger Vouchers ledger not in its trial balance), token, real value, first file_id. Never printed; read only by `fin query export` | D2 |
| `ledger_accounts` (**built**) | Tally ledger (token for party ledgers), group, parent group, status (pending, proposed, confirmed, rejected), mapped Schedule III account with its statement and side (proposed by Claude, confirmed by the user in review, `fin review ... --ledgers`), confidence, reason. Present in TB / in Ledger Vouchers is read from those tables (`fin map ledgers` shows it) | D2 |
| `trial_balance` (**built**) | file_id, sheet, row, period, row type (group or ledger), group, ledger, closing debit, closing credit. No opening or movement columns: the export has none | D2 |
| `ledger_entries` (**built**) | file_id, sheet, row, ledger (from the block header), date, particulars, voucher type, voucher no, debit, credit | D2 |
| `ledger_blocks` (**built**) | file_id, sheet, row, ledger: one per ledger block in a Ledger Vouchers export, with or without voucher lines; G1's "every ledger is in the Ledger Vouchers" reads it | D2 |
| `register_lines` (**built**) | file_id, sheet, row, kind (sales/purchase), bill id, purchase id, customer id, customer (token, sales), vendor id, vendor (token, purchase), location, invoice no, contract start, contract end, invoice date, order type, product, service start, service end, quantity, rate, amount, currency, currency rate, amount in Rs (before GST), service type, handler and submitted-by (tokens) | D3 |
| `bank_statements` (**built**) | file_id, bank, account, currency, period, opening and closing balance (from the header or the rows), debit and credit totals, line count, ties, reader (excel or the PDF add-on), saved-parse path | D3 |
| `bank_lines` (**built**) | file_id, sheet or page, row, bank, account, currency, date, remarks (names as tokens), reference, debit, credit, balance | D3 |
| `payroll_lines` (**built**) | file_id, sheet, row, snapshot date, employee name and code (tokens), designation, department, vertical, sub-vertical, joining date, resign date, gross salary and CTC (annual, unit confirmed by the user). No month column | D3 |
| `recon_runs`, `recon_items` (**built** for `tb_vs_statements`, `bank_vs_invoices`, `bank_vs_tb`; D5 added `fx`, `match_pass`, `currency`, `amount_inr`, `note`, `recon_match_lines` and `match_proposals`) | each reconciliation run (name, period, scope, left file, threshold, hash of its inputs, unmapped count) and its lines (`matched`, `difference`, `left_only`, `right_only`, `unmapped`; D5 adds `fx`), each with both sources, both amounts, the difference and its query | D2, D4, D5 |
| `query_items` | query_id, recon item id: the lines an issue groups; count and value per kind of difference come from it | D4 |
| `adjustments` (changed, **built**) | adds revenue effect and cost effect; the EBITDA effect (`amount`) must equal revenue effect minus cost effect; a reclassification between cost accounts names both (`account`, `from_account`) and the amount moved (`moved_amount`) | D4 |
| `passthrough_customers` (**built**) | engagement_id, customer token, source of the list, run | D4 |
| `register_lines.period` (**built**) | the engagement period of the invoice date (annual period or stub; NULL outside), set at load | D4 |
| `contracts` (**built**) | contract_id, customer (token), start, end, value, billing terms, source (file + page), read_by; optional evidence for the recast | D6 |

Planned views:

- `stub_figures` (D2, **built**): the stub's trial balance rolled up through `ledger_accounts` to Schedule III accounts,
  labelled unaudited management accounts. The audited years keep their statement figures.
- `revenue_split` (D4, **built**, `diligence_split_views.sql`): revenue per period as core and pass-through (project revenue), with the
  Purchase Id link to the OEM purchase (`passthrough_lines` has each link); growth on core revenue only.
- `adjusted_ebitda` (changed, D4, **built**): adds adjusted revenue, and margins on adjusted revenue.
- `nwc_year_end` (D7, **built**, `diligence_nwc.sql`; also `nwc_year_end_lines`, `nwc_monthly_ledgers`, `nwc_monthly_lines`, `nwc_series`): working capital at each year-end (and the stub end) from TB closing balances mapped to
  working capital accounts.
- `nwc_monthly` (D7, **built**): monthly balances from Ledger Vouchers: the prior year-end TB closing as the opening, plus
  cumulative movements to each month end, for working capital ledgers whose movements tie to the TB (`gl_vs_tb`,
  D4). The first year in scope needs the prior year's TB as its opening; without it that year has no monthly view
  (an opening inferred from the same movements would make the check circular). There are no monthly trial
  balances, so this is the only monthly view.

## 5. Gates

The diligence user reviews ingestion, reconciliation and recasting, and no query goes out without their approval.
The gates follow that.

| Gate | Checklist fin evaluates | Unlocks | Status |
|---|---|---|---|
| G1 Ingestion | Every annual period in scope has a loaded document with figures for it; no held documents; no mapping items waiting for review. Its inputs include the figures (`current_facts`), so a changed figure reopens it. D2 adds: a trial balance and Ledger Vouchers per period (and for the stub); every ledger mapped; every TB ledger present in Ledger Vouchers; each audited year's TB ties to its statements (each account difference at or above the threshold is in a query). The stub has no audited statement: its TB is checked against Ledger Vouchers only, and it is marked unaudited. D3 adds: payroll unit confirmed (**built**, only when a payroll file is loaded, `fin/diligence/payroll.py`); every bank parse ties (**built**, only when a bank statement is loaded, `fin/diligence/bank_checks.py`: an accepted `bank_tie` finding counts; overlaps and balance gaps are listed, not blocking) | approving G2 | **Built** as "Ingestion": statement checks, and D2's trial balance checks once the engagement has a source file or a stub (`fin/diligence/tb_checks.py`) |
| G2 Reconciliation | No query in draft. A new or reworded query reopens it; sending, answering, closing or withdrawing one does not. D4–D5 add: every required reconciliation ran; every line at or above the threshold is in a query (`fx` lines are listed, not queried). **D5 built**: with a bank statement loaded, `bank_vs_invoices` and `bank_vs_tb` ran on current data and their lines are queried (`bank_checks_g2.py`) | `fin adjust accept\|reject` | **Built** as "Reconciliation" |
| G3 Recast | Every sales line at or above the threshold has service start and end dates, or is in a query; the recast ran for every period; each period's recast difference has a proposed or decided `recast` adjustment. Contracts are optional evidence | approving G4 | **Built** (`fin/reconcile/recast_checks.py`; [decisions/d6-recast.md](decisions/d6-recast.md)) |
| G4 Adjustments | No proposed adjustment; every accepted one has evidence | `fin engagement close` | **Built** |

There is no report gate: the planned G5 is dropped. The diligence report (D7) shows the gate states it was rendered
with. G1 and G2 are named "Ingestion" and "Reconciliation" (#68).

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

Error codes (docs/errors.md): E801 a gate the command needs is not approved; E802 a gate cannot be approved
(checklist fails, or an earlier gate is not approved); E803 no open engagement, or one already open; E804 an
adjustment is not valid (no evidence, category not in config, period not in the engagement, unknown account); E805
an unknown query, adjustment or finding id, or a status change not allowed; E806 `fin sql --write` given a statement
that writes to a diligence table.

**Planned:**

| CLI | Phase |
|---|---|
| `fin engagement open <co> --periods ... [--stub FY27-YTD-SEP]` | D2 (**built**) |
| `fin ingest <co> <file> --kind trial_balance\|ledger_vouchers\|registers\|bank\|payroll [--profile]` | D2 (**built**: `trial_balance`, `ledger_vouchers`), D3 (**built**: `registers`, `bank`, `payroll`) |
| `fin map ledgers <co> [file.json]` (Tally ledger → account; the user confirms with `fin review confirm <co> <ids> --ledgers`) | D2 (**built**) |
| `fin payroll confirm-unit <co> <file> --annual\|--monthly` (the user says which) | D3 (**built**) |
| `fin engagement passthrough <co> --customer CUST-0001 ... --source "..."` (the user's SI/reseller list, one `--customer` token each; fin prints the tokens it matched) | D4 |
| `fin reconcile <co> [name] [--period]` (**built** in D2 for `tb_vs_statements`, in D5 for `bank_vs_invoices` and `bank_vs_tb`), more reconciliations; `fin reconcile <co> match [--propose <file.json>]` and `fin review list|confirm|reject <co> --matches` (**built**, D5) | D2, D4, D5 |
| `fin query raise <co> --items <recon item ids>` (one issue, many lines) | D4 |
| `fin adjust propose ... --revenue N --cost N` | D4 |
| `fin recast run <co> [--period]`, `fin recast contracts <co> [<file> --reading <csv>]` | D6 (**built**) |
| `fin report <co> --diligence` | D7 (**built**) |
| `fin query export <co> [--brief]` | D8 |

Built in D2: E807 no column profile for the layout (unknown, invalid, or none matches; lists the header lines found),
E808 the export's period missing or not a year of the engagement, E809 a trial balance with group rows only, E810
`fin sql` naming `mask_values`, E811 Ledger Vouchers before their period's trial balance; and E812 a reconciliation
that cannot run (a period not annual in the engagement, no loaded trial balance, or no audited statement figures).
New codes continue from E813.

## 7. Phases

One PR per phase, each small enough to review in one sitting, with tests on synthetic fixtures only. Fixtures use the
column headers received on 2026-11-24 (§9) and made-up names.

### D0: Confirm requirements (no code)
Two rounds of answers were received on 2026-11-24 (§9), with the column headers for every Excel format, and the
owner settled the LlamaParse question the same day. No sample PDFs: the diligence user tests bank PDFs on their own
files once D3 is built.

**Done when:** no question in §9 is open. Done.

### D1: Foundation — done (#43, #44, #45)
Engagement, accountant queries, EBITDA adjustments with evidence, `diligence_events`; the views `adjusted_ebitda`,
`ebitda_bridge`, `ebitda_to_cash` and `fin engagement bridge`; gates G1, G2, G4 with `fin gate`, E801/E802 refusals
and the approval hook; `fin status` summary; `CLAUDE.md` "Diligence" rules and `/diligence`.

Adjustments are effects on EBITDA, so there are no `adjusted_facts` / `adjusted_metrics` views (every metric on
adjusted facts). D4 adds revenue and cost effects, which is enough for adjusted revenue and margins.

### D2: Trial balance, Tally Ledger Vouchers, masking, stub
Status: **built** (first half: source files, the tabular loader and profiles, `trial_balance`, `ledger_entries`,
masking; second half: `ledger_accounts` and `fin map ledgers`, `tb_vs_statements` with `fin reconcile`, the stub
and `stub_figures`, G1's trial balance checks; `docs/product.md` "Diligence source files"). Ledger Vouchers need their
period's trial balance loaded first (E811), so party ledgers are known before they are masked; a Ledger Vouchers
ledger not in its trial balance is masked as `LEDG-0001`. While any trial balance ledger is unmapped,
`tb_vs_statements` drafts no query (the difference may be fin's own mapping gap). The profit and loss ledgers' net
also rolls into `other_equity`, since Tally lists the year's income and expenses beside the opening Profit & Loss
A/c. G1's trial balance checks apply once the engagement has a source file or a stub, so an engagement on statements
only keeps its D1 checklist.
- A tabular loader (`fin/extract/ledger.py`) driven by column **profiles** (`templates/profiles/*.toml`: header
  names → fields, date format, debit/credit or signed amount, unit, which fields are masked). An unknown layout
  fails with a new error code and lists the headers it found, so Claude can write a profile.
- **Trial balance** (Tally Excel, full year or stub): Particulars, closing debit and credit, under export-period
  header lines. The period is read from the header and must be a period of the engagement. Group rows are told
  apart from ledger rows by the export's layout (the profile says how). A group that does not equal the sum of its
  ledgers is a finding, not a load failure: the rows are kept as filed. A TB with group rows only fails with a
  message asking for the Detailed export. fin reports the ledgers present. A prior year-end TB may be loaded as the
  opening for the first year in scope (balance-sheet ledgers in `gl_vs_tb`, `nwc_monthly`).
- **Ledger Vouchers**: one export of all ledgers, in ledger-name blocks (Date, Particulars, Vch Type, Vch No.,
  Debit, Credit), balance-sheet ledgers included. Each row takes its ledger from the block header. fin reports the
  ledgers present and lists TB ledgers missing from it, so Claude asks the user for the export with all ledgers.
- **Masking** (§3): party ledgers are tokenised on load; `mask_values` holds the real names; `fin sql` refuses to
  read it.
- TB ledgers map to Schedule III accounts (`ledger_accounts`), proposed by Claude and confirmed in review.
- Built-in reconciliation `tb_vs_statements`: TB closing balances rolled up by account vs the audited statement
  facts, per audited period; one line per account. The stub has no statement: `stub_figures` gives its figures,
  labelled unaudited.
- `fin engagement open --stub`; G1 Ingestion gains the trial balance checks (§5).

**Done when:** a synthetic TB and Ledger Vouchers load with sheet!row lineage and masked party names; a group-only TB
is refused; a planted account difference at or above the threshold becomes a draft query, and one below it is listed;
a synthetic stub loads and is labelled unaudited.

### D3: Registers, bank, payroll
Status: **built and reviewed as a whole** (#72–#76 and the review that followed): registers (`--kind registers`,
`fin/extract/registers.py`, `fin/diligence/registers.py`, `templates/profiles/billing_registers.toml`,
duplicate-invoice finding), bank statements (Excel and PDF), payroll. Product-level decisions: [decisions/d3-registers.md](decisions/d3-registers.md),
[decisions/d3-bank.md](decisions/d3-bank.md), [decisions/d3-payroll.md](decisions/d3-payroll.md),
[decisions/d3-review.md](decisions/d3-review.md). The golden evaluation has a check kind for each output
(`bank_closing`, `register_total`, `payroll_total`).
- **Registers**: one workbook from the billing system, a sales sheet and a purchase sheet, headers as in §9
  (`register_lines`, §4). Amount in Rs is before GST; there are no GST or tax columns. Customer, vendor, handler and
  submitted-by are masked. Built: the headers of `billing_registers.toml` are synthetic (the real export's headers were
  not in §9 as columns): replace them when the real export is seen. The same invoice number twice is a finding (sales
  by number, purchases by vendor and number), both rows kept.
- **Bank**: Excel profiles for ICICI, SBI and Axis (INR and USD accounts). Remarks keep their text with known names
  replaced by tokens.
- **PDF bank statements: the chosen PDF parser** (owner decision, 2026-11-24). fin reads a bank PDF's tables with
  the parser in `config.toml` (`[pdf] parser`: Docling, LiteParse or LlamaParse; the setting is built in a separate
  PR) and maps them to `bank_lines` with the same profiles as the Excel statements, keeping file + page lineage.
  LiteParse is the default for bank PDFs: nothing leaves the machine.
  - Bank PDFs go through whichever parser the user has chosen, LlamaParse included (owner, 2026-11-24). LlamaParse
    uploads pages to a cloud service (README, "Install"), so a user picks a local parser for documents that must
    not leave the machine. LlamaParse's API key comes from the environment (`LLAMA_CLOUD_API_KEY`), never from the repo or the data folder.
    A missing key or a failed call raises an error with the run_id; nothing is loaded.
  - A parse is saved in the data folder with the file's sha256, so ingesting the same file again does not parse it
    again, and every loaded row can be traced to the parse it came from.
  - A parse whose rows do not tie (opening balance + credits − debits ≠ closing balance) is a finding, not a load.
  - Tests use recorded parser output for a synthetic PDF; CI never calls the LlamaParse API.
- **Payroll** (**built**, `docs/decisions/d3-payroll.md`): the Sparsh snapshot, one row per employee; gross salary and CTC are annual. fin asks the user to
  confirm the unit (`fin payroll confirm-unit`) and warns when the amounts look monthly. Employee name and code are
  masked. The separate monthly salary workbooks are out of scope for now: a later optional profile.
- Duplicate and overlap checks: the same invoice number twice, overlapping bank periods, a gap in bank balances
  (closing ≠ next opening). These are findings, then queries.

**Done when:** each synthetic file loads with names masked; the planted duplicate invoice, bank balance gap and a
parse that does not tie are found; a payroll file whose amounts look monthly is warned about.

### D4: Register ↔ ledger ↔ accounts reconciliation; pass-through; adjustment effects
Status: **reconciliations built** (`fin/reconcile/`: `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`, `query_items`, `fin query raise --items`, G2's reconciliation checks, E840–E842; choices: `docs/decisions/d4-recon.md`); pass-through, `revenue_split` and adjustment effects are built by their own workstream.
The pass-through and adjustment-effect part is **built** (`docs/decisions/d4-passthrough.md`).
- `sales_register_vs_gl`, `purchase_register_vs_gl` (monthly, then by party, then by invoice), `gl_vs_tb` (Ledger
  Vouchers movements vs the change in TB closing from the prior year-end; a balance-sheet ledger with no prior TB is
  listed as not reconciled), `payroll_vs_gl` (annual). Deterministic SQL, one function per reconciliation
  in `fin/reconcile/`. Registers compare on Amount in Rs (before GST).
- USD invoices: the register's Amount in Rs (Amount × Currency Rate) is compared with the books; the difference
  from the ledger's conversion rate is an `fx` line, shown as FX gain or loss, never plugged.
- Threshold per line (§3). Claude groups the lines at or above it into issues (`fin query raise --items`); the user
  approves them. G2 Reconciliation gains the reconciliation checks (§5).
- Pass-through: `fin engagement passthrough` records the user's customer list; `revenue_split` shows core and
  pass-through (project) revenue, with the Purchase Id link to the OEM purchase; growth uses core revenue. Claude
  proposes the `normalisation` adjustment that strips pass-through revenue and its cost.
- Adjustments gain revenue and cost effects (§4); `adjusted_ebitda` gains adjusted revenue and margins on it. The
  default `adjustment_categories` drops `run_rate` and `pro_forma` (a small config change).
- The stub is cross-checked: its TB vs Ledger Vouchers and vs the registers, here; vs bank in D5.

**Done when:** every planted difference in the synthetic set is found, with both sources, and nothing else is; a
planted USD rate difference shows as an `fx` line; a pass-through customer's revenue is split out and the
reclassification adjustment changes margins with zero EBITDA effect.

### D5: Bank ↔ receipts ↔ invoices — built
Status, 2026-11-25: built (`docs/decisions/d5-bank-match.md`; `fin/diligence/bank_*.py`). The stub's bank cross-check is `bank_vs_tb`.
- Matching in fixed passes: exact reference, then amount + party + date window (config), then amount + date
  window. Each match records the pass that made it. Unmatched items become `left_only` / `right_only`.
- Axis USD receipts match on customer, USD amount and a date window; the booked rupee value is compared, and a
  difference is an `fx` line (FX gain or loss), never plugged.
- Customer-level: invoices vs receipts → collection days and overdue ageing.
- No fuzzy or ML matching. What the passes don't match goes to Claude, which proposes matches through
  `fin reconcile match --propose <file.json>`, and the user confirms them (as with mappings).

**Done when:** the synthetic matching answer key, including a USD receipt with an FX difference, is reproduced exactly.

### D6: Service-period recast
Status: **built** (`fin recast run|contracts`, `fin/reconcile/recast*.py`, `fin/reconcile/contracts.py`, gate G3, E863-E865, golden eval kinds `recast_revenue`, `recast_difference`, `recast_undated`; `docs/product.md` "Service-period recast"; every design choice and how to change it: [decisions/d6-recast.md](decisions/d6-recast.md)).
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
Status: **built** (`db/schema/diligence_nwc.sql`, `fin/report/dd_*.py`, `templates/report/diligence.qmd`, `dashboards/pages/diligence.md`,
`fin report <co> --diligence`; E873–E874; choices: `docs/decisions/d7-qoe.md`). The report also has the bank collections and
ageing (D5), the customers, sectors and pricing section (D9), the service-period recast (D6), the reconciliation and
query status, the NWC tables and the sources appendix. Golden checks: `nwc_year_end`, `nwc_monthly`.
- Normalised working capital from year-end trial balances (`nwc_year_end`), and monthly from Ledger Vouchers
  movements (`nwc_monthly`, §4). The EBITDA bridge and EBITDA-to-cash walk are built (D1).
- A diligence report template (`templates/report/diligence.qmd`) and a dashboard page, with the same narrative
  rules as the health report: describe, don't judge; numbers only as references. Adjusted base EBITDA, other income
  below it, pass-through shown as project revenue, the stub labelled unaudited management accounts, and the gate
  states it was rendered with. Names appear as tokens.

**Done when:** the synthetic engagement renders a diligence report whose every number traces to source rows.

### D8: Query export
Status: **built** (`fin/diligence/export*.py`, `answers.py`; E858–E861; choices: `docs/decisions/d8-query-export.md`).
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
**Status: built** (`fin analytics`, `fin map customers`, `docs/decisions/d9-analytics.md`).
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

**Resolved by the owner on 2026-11-24:**

1. **LlamaParse.** Bank PDFs carry names. They go through whichever PDF parser the user has chosen (`[pdf] parser`),
   LlamaParse included. The README ("Install") says LlamaParse uploads pages to a cloud service, so a user picks
   a local parser (Docling or LiteParse) for documents that must not leave the machine.
Statement findings (E401–E405) become queries only when the user raises them, as built.

## Commands and behaviour (as built)

A diligence engagement on one company keeps two things beside the reported figures, never changing them: questions
for the company's accountant, and proposed adjustments to EBITDA. A difference is the company's to explain, so it
becomes a query, not a fix. Claude drafts; you decide. fin never sends anything. Plan:
this file.

- `fin engagement open <company> --periods FY24,FY25,FY26 [--threshold N] [--stub FY27-YTD-SEP]`: one open
  engagement per company, with the years in scope, the query threshold in rupees (default `query_threshold` in
  `config.toml`) and optionally a current-year stub: the year to date after the last year (here April to
  September of FY27), from unaudited management data. The stub is labelled "unaudited management accounts"
  wherever it shows and is never annualised.
  `fin engagement status <company>` counts queries and adjustments by status; `fin engagement close <company>`
  (needs gate G4, unless the engagement has no queries and no adjustments).
- Queries: `fin query raise <company> --finding <id> --question "..."` (the finding's period, or for a bank
  finding the period its statement was loaded into, its difference and sources; the finding is not changed) or
  `--amount N --period P --source "file sheet!cell" --question "..."` (P a year of the engagement or its stub, else E881).
  A token in the wording that is not one of the company's masked names is refused at raise and edit (E861).
  A query starts as a draft (`fin query edit` changes its wording), then `fin query approve`, `sent` (when you have
  sent it), `answer --text` (or `--file reply`: archived like a source; a .txt, .eml or .md reply is read with known names masked, any other file needs `--text`), `close`; `withdraw --reason` any time before it is closed; `list [--status]`.
  `fin query export <company> [--brief]` writes the approved queries for the accountant to `exports/<company>/<run_id>/`
  of the data folder: `queries.xlsx` (summary, a sheet per query with the count and value per kind of difference, and the
  cited source sheets with the lines highlighted) and `email.txt` (a draft; you send it from your own email, then `fin query
  sent`), or with `--brief` a Word brief per query. The files hold the real names; fin prints only their paths (E858-E861).
- Adjustments: `fin adjust propose <company> --period --scope --category --description` with evidence
  (`--query`, `--finding` or `--source`), the effects (`--revenue-effect` and `--cost-effect`, signed rupees; the
  EBITDA effect, `--amount`, is always revenue effect minus cost effect, and `--amount` alone is a cost effect of
  minus that) and optionally `--account`. A reclassification between two cost accounts names both
  (`--account` the cost moves to, `--from-account`, `--moved-amount`) and has no net effect. Then
  `fin adjust accept <ids>` or `reject <ids> --reason`; `list [--status]`. Categories are `adjustment_categories`
  in `config.toml`.
- Pass-through: `fin engagement passthrough <company> [--customer CUST-0001 ... --source "..."] [--clear]` records your
  list of pass-through customers (SI/reseller deals at about zero margin, as tokens from the sales register; E845 for a
  token not on a loaded sales register), replacing the earlier list; without options it shows the list and the
  revenue split. The views `revenue_split` (per period: sales revenue as core and pass-through, the pass-through
  lines with and without a Purchase Id, the linked OEM purchase cost, core revenue growth) and `passthrough_lines`
  (each pass-through line with its OEM purchase) read it. Growth is on core revenue only. Claude proposes the
  `normalisation` adjustment that strips pass-through revenue and its cost; you accept it. `fin` values nothing.
- `fin engagement bridge <company> [--period FY25] [--scope standalone] [--json]`: per period and scope of the
  latest engagement, the EBITDA bridge (reported EBITDA, each accepted adjustment, adjusted EBITDA; proposed ones are
  listed as not applied) and the EBITDA-to-cash walk down to CFO and free cash flow, with cash conversion. Money in
  the documents' unit, as `fin statements`; `--json` in rupees. A line whose input is missing shows `-` and names it.
  The same figures are the views `adjusted_ebitda` (also adjusted revenue and the margin on it), `ebitda_bridge` and
  `ebitda_to_cash`, queryable with `fin sql`.
- Service-period recast: `fin recast run <company> [--period FY25]` spreads each sales register line's Amount in Rs over its
  service start and end dates (by service day) and compares each year's recast with the revenue booked by invoice date.
  The difference is proposed as a `recast` adjustment (revenue effect = EBITDA effect); running it again replaces a stale
  proposal of its own and never overrides one you accepted or rejected. A sales invoice with no service dates is recognised on
  its invoice date and listed (at or above the threshold it goes into a query, `fin query raise --items`). The unaudited stub is
  not recast. `fin recast contracts <company> <file> --reading <csv>` loads Claude's reading of a contract (file + page) as
  evidence shown beside an undated invoice; it never drives the schedule. Decisions: `docs/decisions/d6-recast.md`.
- Gates: checkpoints you approve, in order. fin checks each one's checklist; Claude prepares a gate and shows you
  `fin gate status <company>` (each gate's state, its checklist lines and what it unlocks), and only you approve it
  (`fin gate approve <company> <gate>`). A Claude Code hook asks you before any command Claude runs that looks like
  a gate approval, and `fin sql --write` cannot write gate approvals, queries, adjustments or the engagement
  (E806): they change only through `fin engagement|query|adjust|gate` (and the source file tables only through
  `fin ingest --kind`). An approved gate reopens on its own when the
  data under it changes, and you approve it again.
  - **G1 Ingestion:** every year in scope has mapped figures from a loaded document, no held documents, no mappings
    waiting for review. Once the engagement has a trial balance (or a stub), also: a trial balance and Ledger
    Vouchers for every year and the stub, every trial balance ledger in its Ledger Vouchers, every ledger mapped to
    an account, and each year's `tb_vs_statements` run on the current data with every difference at or above the
    threshold in a query (the stub is checked against its Ledger Vouchers only). A changed figure (for example a
    fix with `fin sql --write`), a new source file, a mapping change or a new reconciliation reopens it, and so
    does any change after which its checklist fails. Once a payroll file is loaded, G1 also needs the user to
    have confirmed its unit (`fin payroll confirm-unit`).
  - **G1 and bank statements:** once a bank statement is loaded, also every bank parse ties (opening + credits −
    debits = closing, or the user has accepted its finding). Overlapping periods and balance gaps are findings for
    queries, listed in G1 but not blocking it.
  - **G2 Reconciliation:** no query in draft. A new or reworded query reopens it; sending, answering or closing an
    approved query does not. Needed by `fin adjust accept|reject`.
  - **G3 Recast:** once a sales register is loaded, for every year: `fin recast run` ran on the current data, every sales
    invoice at or above the threshold with no service dates is in a query, and the year's recast difference has a
    proposed or decided `recast` adjustment. Needed to approve G4.
  - **G4 Adjustments:** no proposed adjustment, and every accepted one has evidence. Needed by
    `fin engagement close`.

  A command that needs a gate that is not approved refuses with E801; a gate that cannot be approved yet, E802.
  `fin status` shows an open engagement's queries, proposed adjustments and gates.
- Source files: `fin ingest <company> <file> --kind trial_balance|ledger_vouchers|registers|bank|payroll [--profile <name>]` loads a Tally
  Trial Balance (Detailed mode) or Ledger Vouchers export into `trial_balance` or `ledger_entries` (and `ledger_blocks`, every ledger block the export has, even one with no voucher lines), or a billing-system
  registers workbook (a sales sheet and a purchase sheet) into `register_lines`, each row with
  its file (`source_files`: sha256, archive copy, profile) and sheet!row. The period comes from the export's header
  lines and must be a year of the open engagement, or exactly its stub (E808). A column profile (`templates/profiles/*.toml`) says how
  to read the layout; a layout no profile matches fails with E807 and lists the header lines found, so Claude can
  write a profile. A trial balance with group rows only is refused (E809: export it in Detailed mode); a group not
  equal to the sum of its ledgers is a finding, kept as filed. Load a period's trial balance before its Ledger
  Vouchers (E811); fin then lists the trial balance ledgers missing from the Ledger Vouchers. A later file of the
  same kind and period supersedes the earlier one. A registers workbook has no period header: its period comes from
  its invoice dates (a later registers file replaces the earlier whatever its period). Amount in Rs is before GST.
  The same invoice number twice (sales: the number; purchases: the vendor and number) is a finding, both rows kept
  (E813 only when a row is not an invoice line: no number, date, party or Amount in Rs, or a date in another format).
- Bank statements: `fin ingest <company> <file> --kind bank [--profile <name>]` loads an Excel or PDF statement (ICICI,
  SBI or Axis; INR or USD accounts, the profiles `templates/profiles/bank_*.toml`) into `bank_statements` and
  `bank_lines`, each row with its file and sheet!row (Excel) or page and row (PDF). Remarks keep their text with
  known masked names replaced by tokens. A statement covers part of one period of the engagement (it must not cross
  a year end, E808). A PDF is read by the PDF add-on you chose (`fin parser use`; E816 when there is none): its
  tables are saved in the data folder as `cache/bank/<sha256>.json`, so the same file is never parsed twice and
  every row traces to that parse. LlamaParse uploads the pages to its cloud and needs `LLAMA_CLOUD_API_KEY` in
  the environment (a missing key or a failed call is E206; nothing loaded); use Docling or LiteParse for statements
  that must not leave your Mac. A statement whose opening + credits − debits is not its closing balance is a
  finding (`bank_tie`), as are statements of one account that overlap (`bank_overlap`) or whose balances do not
  join (`bank_balance_gap`: a statement or transaction may be missing). Findings, not failures: the rows stay as
  filed and you decide (`fin findings accept`, or a query to the accountant).
- Names: party ledgers (under Sundry Debtors and Sundry Creditors) are stored and printed as tokens (`CUST-0001`,
  `VEND-0001`), the same name the same token in every file; register customers, vendors, handlers and submitted-by
  are tokens too (`CUST-`, `VEND-`, `USER-`); known names in Ledger Vouchers particulars are
  replaced too. A Ledger Vouchers ledger not in its trial balance is masked as `LEDG-0001`, since fin cannot tell
  whether it is a party. The real names stay on your Mac in `mask_values`, which `fin sql` refuses to read (E810).
- Payroll: `--kind payroll` loads the Sparsh snapshot (one row per employee, profile `sparsh_payroll`) into
  `payroll_lines`; the snapshot date in its header ("Employee Master as on 31-Mar-2025") must fall in a year of the
  engagement or its stub (E808). Employee name and code are tokens (`EMP-0001`, `ECODE-0001`). Gross salary and CTC are
  read as annual rupees: fin warns when their medians are under Rs 1,50,000 (they look monthly), and you tell it
  the unit with `fin payroll confirm-unit <company> <file> --annual|--monthly`. G1 shows "unit confirmed annual by
  the user" for each payroll file and fails until you confirm; `--monthly` keeps it failing (fin does not convert).
  A row with no employee name or code, or a bad date, is E821 and nothing loads.
- Ledger mapping: each trial balance ledger maps to a Schedule III account. `fin map ledgers <company>` lists the
  ledgers waiting; Claude proposes accounts with `fin map ledgers <company> <file.json>`; you confirm or reject
  them with `fin review list|confirm|reject <company> ... --ledgers` (`--account` to choose another). fin never
  confirms one itself.
- Customer analytics (D9): `fin analytics <company> [--json]` shows, for the latest engagement's sales register (Amount in Rs,
  before GST; customers are tokens): customer concentration (top 1, 5 and 10 share), retention of core customers against
  the previous year (net, gross and logo; cohorts by first year), sector mix, and price, volume and mix of core products
  that have a quantity and a rate. Core and pass-through revenue are separate segments; retention, cohorts and price,
  volume and mix are core only, annual only (E869: no sales register in the engagement). The sector of a customer comes
  from you: `fin map customers <company>` lists the customers waiting, Claude proposes with `fin map customers <company>
  <file.json>` (`{customer, sector, confidence, reason}`), you confirm or reject with `fin review list|confirm|reject
  <company> ... --customers` (`--sector` to choose another; E868). A customer without a confirmed sector is shown as
  `unmapped`. The views are `customer_concentration`, `customer_retention`, `customer_cohorts`, `sector_mix`,
  `product_revenue`, `product_pvm`; the dashboard has a "Customers and sectors" page and the health report a "Customers,
  sectors and pricing" section when the company has an engagement with a sales register. Decisions:
  `docs/decisions/d9-analytics.md`.
- Diligence report (D7): `fin report <company> --diligence [--scope] [--render]` writes `diligence_narrative.md` (Claude fills it with
  `/diligence`, numbers only as `{{figure:<name>:<period>}}` references), then renders `diligence.html|docx|pdf` in `reports/<company>/<scope>/`:
  adjusted EBITDA and the bridge, adjustments with evidence (proposed ones not applied), other income, core and project revenue,
  the recast, the EBITDA-to-cash walk, working capital (year-end and stub from the trial balances; monthly from the Ledger Vouchers
  for ledgers that tie, E873-E874), reconciliations and queries, collections and ageing, customers and sectors, the stub as unaudited
  management accounts, the gate states it was rendered with and a sources appendix. The dashboard has a "Diligence figures" page.
  Decisions: `docs/decisions/d7-qoe.md`.
- `fin reconcile <company> [name] [--period FY25] [--json]` (name: `tb_vs_statements`, `sales_register_vs_gl`,
  `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`; none: every one whose inputs are loaded). The last four compare
  the registers, Ledger Vouchers, trial balance and payroll; fin drafts no query for them: Claude groups the lines
  at or above the threshold into issues with `fin query raise <company> --items 12,13 --question "..."` and you approve
  them; foreign-currency rate differences are `fx` lines, listed, never queried (E840-E842). `tb_vs_statements`: each year's trial balance, rolled up by
  confirmed ledger mapping, against the audited standalone statement figures, one line per account (the year's
  profit from the income and expense ledgers rolls into other equity, as the balance sheet shows it). A line whose
  difference is at or above the threshold becomes a draft query (origin `recon`; you approve and send it); a line
  below it is listed. Ledgers not mapped to an account are flagged and no query is drafted until every ledger is
  mapped. Nothing is changed to make it tie. The stub has no audited statement: its figures are the view
  `stub_figures`, labelled unaudited (E812 if asked to reconcile it).
- `fin reconcile <company> bank_vs_invoices [--period]`: bank receipts (credits of the loaded statements) against sales
  invoices, in fixed passes: the invoice number in the receipt's reference or remarks, then same customer + amount +
  dates within the engagement's `match_window_days` (copied from `config.toml` when it opens), then same amount + dates within the window when neither has another candidate.
  Each match records its pass; what is left is a receipt without an invoice (`left_only`) or an invoice without a
  receipt (`right_only`). A foreign-currency receipt matches on customer, own-currency amount and date; its booked
  rupee value (the Ledger Vouchers receipt on the customer's ledger, within `fx_window_days`) is compared with the
  invoice's Amount in Rs and a difference is an `fx` line (gain or loss), listed and never queried or plugged. Lines at
  or above the threshold in rupees are for Claude to group into queries (`fin query raise --items`; an invoice not yet past
  `invoice_due_days` is listed only). The view
  `customer_collections` gives collection days and overdue ageing. What the passes leave goes to Claude:
  `fin reconcile <company> match --propose <file.json>`; you confirm with `fin review list|confirm|reject <company>
  --matches`, and the next run applies the confirmed ones. `fin reconcile <company> bank_vs_tb` compares the INR
  statements' closing balances at a period's last day with the trial balance's cash and bank ledgers (the stub's
  cross-check against bank). G2 needs both to have run on current data.

Every change, and every gate approval, is recorded in the audit table `diligence_events`.
