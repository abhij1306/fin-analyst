---
description: A diligence engagement: accountant queries, EBITDA adjustments with evidence, and gates
argument-hint: <company> [what to do]
---

Diligence work on a company: $ARGUMENTS

Read the "Diligence" rules in `CLAUDE.md` first. In short: a difference in the company's data is queried, never
fixed; you draft and propose, the user decides; fin never sends anything.

1. **Where it stands.** Run `uv run fin engagement status <company>`. If it fails with E803 (no open engagement), ask
   the user which annual periods are in scope (e.g. FY24,FY25,FY26) and whether to use the default query threshold
   (`query_threshold` in `config.toml`), and whether there is a current-year stub (e.g. April to September after the
   last year: `FY27-YTD-SEP`), then run `uv run fin engagement open <company> --periods FY24,FY25,FY26
   [--threshold N] [--stub FY27-YTD-SEP]`. The stub is unaudited management accounts: say so whenever you show its
   figures (the view `stub_figures`), and never annualise them or use them for growth. Report the engagement, the queries by status and the adjustments by status with their sums,
   from the command output.
   - **Source files.** Load each period's Tally trial balance (Detailed mode) first, then its Ledger Vouchers:
     `uv run fin ingest <company> <file> --kind trial_balance`, then `--kind ledger_vouchers` (E811 if the trial
     balance is not loaded yet). Report the outcome lines as printed: ledgers and groups, findings (a group not
     equal to its ledgers: show it with `fin findings list` and ask, as for any finding), the trial balance ledgers
     missing from the Ledger Vouchers (ask the user for the export with all ledgers) and the ledgers masked as
     `LEDG-...`. E807 lists the header lines of a layout no profile reads: write a profile in
     `templates/profiles/` (`docs/development.md`) and ingest with `--profile <name>`. E808: the period is not a
     year of the engagement nor exactly its stub; E809: ask for the Detailed export.
   - **Payroll.** Load the Sparsh snapshot with `uv run fin ingest <company> <file> --kind payroll` (its header gives
     the snapshot date, which must fall in a year of the engagement or the stub). Report the outcome lines as printed,
     including a `WARNING: the amounts look monthly` line. Gross salary and CTC are read as annual: ask the user
     whether they are annual or monthly, and run `uv run fin payroll confirm-unit <company> <file> --annual` (or
     `--monthly`) only when the user says which; never decide the unit yourself. `--monthly` keeps G1 failing: fin does
     not convert, so ask the company for an annual snapshot. E820: no loaded payroll file of that name or id; E821: a
     row with no employee name or code, or a bad date (a corrected file is the company's to send).
   - **Registers.** The billing system's workbook (a sales sheet and a purchase sheet) loads with
     `uv run fin ingest <company> <file> --kind registers`: it needs no trial balance and has no period header (the
     period comes from its invoice dates). Report the outcome lines as printed: lines per sheet, names masked, lines
     outside the engagement. A repeated invoice number is a finding (`register_duplicate_invoice`, both rows kept):
     show it with `fin findings list` and ask the user, then draft a query from it as in step 2. E813: a row is not
     an invoice line (the message names sheet!row); E807: no profile reads the layout (write one).
   - **Bank statements.** `uv run fin ingest <company> <file> --kind bank` (Excel or PDF; ICICI, SBI or Axis, INR or
     USD). A PDF needs the user's PDF add-on (E816: `fin parser use`); LlamaParse uploads the pages, so say so
     before using it on a statement and prefer a local parser, and it needs `LLAMA_CLOUD_API_KEY` (E206). A
     statement that does not tie (opening + credits − debits ≠ closing), statements that overlap and a balance gap
     between statements are findings, not failures: show them neutrally with `fin findings list` and ask the user
     (accept as filed, or a query); never edit bank rows to make them tie. G1 needs every bank parse to tie or
     its finding accepted by the user.
   - **Names are tokens** (`CUST-0001`, `VEND-0001`, `LEDG-0001`, `EMP-0001`, `ECODE-0001`). Use them as they are in queries and answers;
     never try to read `mask_values` (`fin sql` refuses, E810) or guess a name.
   - **Ledger mapping.** `uv run fin map ledgers <company>` lists the trial balance ledgers waiting for an account.
     Propose one per ledger from its name and group (`uv run fin map accounts` for the accounts: balance sheet or
     profit and loss). Where the audited statements have the line, propose the account they map it to
     (`uv run fin statements <company>`): a bank ledger goes to `cash_and_bank_balances` when the statements print
     "Cash and bank balances", not to `cash`, or `tb_vs_statements` drafts two queries for one figure.
     Write the proposals in a JSON file `[{"ledger": ..., "account": ..., "confidence": 0-1, "reason": ...}]` and run
     `uv run fin map ledgers <company> <file.json>`. Then show `uv run fin review list <company> --ledgers` and confirm
     or reject only what the user says: `uv run fin review confirm <company> <ids> --ledgers [--account <account>]`,
     `uv run fin review reject <company> <ids> --ledgers`. Never confirm a ledger on your own.
   - **Trial balance vs statements.** Once the ledgers are mapped, run `uv run fin reconcile <company>`
     (`tb_vs_statements`, each audited year). Report its lines as printed: each line at or above the threshold has a
     draft query (origin `recon`; reword it with `fin query edit` if needed, then step 3); lines below are listed:
     show them, do not raise them unless the user asks. Unmapped ledgers are flagged and no query is drafted until
     every ledger is mapped. A difference is queried, never fixed; a wrong mapping (fin's own error) is fixed in
     review and the reconciliation run again. A run then lists `stale draft queries`: drafts fin made from a line
     the new run no longer has. Show them; fin never withdraws a query, so withdraw them only when the user says so
     (`fin query withdraw`). E812: the period has no trial balance or statement figures, or is the
     stub (which has no audited statement).
   - **Register, ledger, trial balance and payroll reconciliations (D4).** `uv run fin reconcile <company> [name]
     [--period FY25]` also runs `sales_register_vs_gl`, `purchase_register_vs_gl` (month, party and invoice
     lines; Amount in Rs before GST), `gl_vs_tb` (Ledger Vouchers movement vs the trial balance change from the
     prior period; a balance sheet ledger with no prior trial balance is `not_reconciled`, listed) and `payroll_vs_gl`
     (annual; the user must have confirmed the payroll unit) for each period (and the stub) whose inputs are loaded.
     These draft nothing. Lines marked "needs a query" (a detail line at or above the threshold) are for you to
     group: one issue per kind of difference (for example every invoice missing from the ledger), drafted neutrally
     with `uv run fin query raise <company> --items 12,13,14 --question "..."` (one period per query; the user
     approves it). `fx` lines (a foreign-currency invoice differing only by the ledger's conversion rate, shown as
     FX gain or loss) and `not_reconciled`, `unmapped`, month and party lines are listed, not queried; never plug
     them. E840: an input is not loaded, a payroll unit is not confirmed annual, or no ledger is confirmed to
     revenue, purchases or employee expense. E841/E842: a line id that is unknown, stale or cannot be raised.
   - **Bank receipts vs invoices.** With a bank statement and the registers loaded, run
     `uv run fin reconcile <company> bank_vs_invoices` (fixed passes: invoice number in the reference, then customer +
     amount + date window, then amount + date window; each match shows its pass). Report its lines as printed:
     receipts with no invoice (`left_only`), invoices with no receipt (`right_only`), differences, and `fx` lines
     (a USD receipt's booked rupees against the invoice: gain or loss, listed, never queried or plugged). Lines at or
     above the threshold need a query (group them with `fin query raise --items`, as above); an invoice not yet due is listed only. The view
     `customer_collections` has collection days and ageing (`fin sql`). For what the passes left, you may propose
     matches: write `[{"bank": [[file_id, "sheet", row]], "invoices": [[file_id, "sheet", row]], "confidence": 0-1,
     "reason": ...}]` using the keys from `fin sql` on `bank_lines` and `register_lines`, and run `uv run fin reconcile
     <company> match --propose <file.json>`. Show `uv run fin review list <company> --matches`; confirm or reject only
     what the user says (`uv run fin review confirm|reject <company> <ids> --matches`), then run the reconciliation
     again. Never fuzzy-match yourself. `uv run fin reconcile <company> bank_vs_tb` compares the statements' closing
     balances with the trial balance (the stub's bank cross-check). G2 needs both to have run on current data.
     E840: a statement, register or trial balance is missing; E854: a bad proposal file; E855: a proposal id that is
     unknown or already decided.
   - **Golden evaluation.** The trial balance ledgers, `tb_vs_statements` differences and stub figures can be scored
     against values a person checked (`/eval`, `docs/EVAL.md`). Never mark a golden line checked yourself.
2. **Queries from findings.** Run `uv run fin findings list <company> --json`. For each open or accepted finding in
   a period of the engagement, with a difference at or above the engagement's query threshold, draft a short,
   neutral question for the company's accountant: what the two figures are, where each is (file + sheet!cell or
   page), and what explains the difference. Do not suggest an answer. Raise it with
   `uv run fin query raise <company> --finding <id> --question "..."`: it copies the finding's period, scope,
   difference and sources; the finding is not changed. A finding below the threshold: list it for the user, do not
   raise it unless they ask.
   - A difference found another way (not a finding): `uv run fin query raise <company> --amount <rupees> --period
     <FY25> [--scope standalone|consolidated] --source "<file> <sheet!cell or page>" --question "..."`. The amount
     comes from `fin` output; never compute it in prose. The period must be a year of the engagement or its stub
     (E881). A token in the wording that the company's masked names do not have is refused (E861): copy tokens
     from `fin` output.
   - To change the wording of a draft: `uv run fin query edit <company> <id> --question "..."`.
3. **Show the drafts** (`uv run fin query list <company> --status draft`) with their amounts and sources, and ask the
   user to approve them. Only when the user approves: `uv run fin query approve <company> <ids...>`.
4. **Export and sending are the user's.** When the user asks for the files to send, run `uv run fin query export
   <company>` (a marked-up workbook `queries.xlsx` and a draft `email.txt` in the data folder; `--brief` for a Word
   brief per query instead). It prints only the file paths: the files hold the real names, which you never see; tell the
   user where they are. E858: nothing is approved yet; E861: a token in the wording has no real name (fix it with
   `fin query edit` while a draft). fin never sends anything. Run `uv run fin query sent <company> <ids...>` only
   when the user says they sent those queries.
5. **Answers.** When the user gives an accountant's answer: `uv run fin query answer <company> <id> --text "..."
   [--source "<email, file or page>"]`, or with the reply file: `--file <path>` (archived like a source; a .txt, .eml or
   .md reply is read with known names masked and is the answer, any other file needs `--text`; E860). Write `--text`
   with tokens, never real names. Then propose what follows (close it; re-reconcile after the company sends a corrected file and the user loads it; raise a follow-up; or an adjustment)
   and close it only when the user says: `uv run fin query close <company> <ids...>`. To drop a query before it is
   closed, when the user says: `uv run fin query withdraw <company> <ids...> --reason "..."`.
6. **Adjustments.** Propose an adjustment only with evidence: an answered query, a finding, or a source reference.
   `uv run fin adjust propose <company> --period <FY25> --scope standalone|consolidated --category <category>
   (--amount <rupees> | --revenue-effect <rupees> --cost-effect <rupees>) --description "..." [--account <account>]
   [--query <id>] [--finding <id>] [--source "..."]`.
   - The effects are signed rupees and must come from `fin` output or the source; never do arithmetic in prose.
     `--revenue-effect` raises revenue when positive, `--cost-effect` raises costs when positive, and the EBITDA
     effect is always revenue effect minus cost effect (fin refuses, E804, an `--amount` that is not). `--amount`
     alone is the effect on EBITDA, recorded as a cost effect of minus that amount (an add-back, a one-off item).
   - **Pass-through.** The user gives the list of pass-through customers (SI/reseller deals at about zero margin,
     as `CUST-` tokens): record it only as given, with where it came from:
     `uv run fin engagement passthrough <company> --customer CUST-0001 --customer CUST-0002 --source "<where the
     list came from>"` (E845: a token that is not on a loaded sales register; without options it shows the list).
     Then show `fin sql` on `revenue_split` (per period: core revenue, pass-through revenue, its licence part (a
     Purchase Id: software or licence) and services part (none), `linked_purchase_cost` and core revenue growth)
     and `passthrough_lines` (each line with its OEM purchase). Growth is on core revenue only. Propose the
     `normalisation` adjustment that strips pass-through per period and scope with the figures from those views:
     `--category normalisation --revenue-effect -<passthrough_revenue> --cost-effect -<linked_purchase_cost>`,
     evidence `--source "revenue_split <period>"` (or a query). Say if a pass-through line has no purchase behind it
     (`passthrough_lines` shows NULL purchase columns): its cost is not in `linked_purchase_cost`. Do not value
     the pass-through revenue or say what multiple it deserves: that is the user's judgement.
   - **Customer analytics.** `uv run fin analytics <company>` shows customer concentration (top 1, 5, 10 share),
     retention of core customers (net, gross, logo; cohorts), sector mix and price/volume/mix of core products, from the
     sales register, with customers as tokens and core and pass-through revenue separate. Sectors come from the user:
     `uv run fin map customers <company>` lists the customers waiting (with what each bought; never guess from a
     name, you only see tokens); propose from the products with `uv run fin map customers <company> <file.json>`
     (`[{"customer": "CUST-0001", "sector": "Banking", "confidence": 0.7, "reason": "..."}]`), show
     `uv run fin review list <company> --customers`, and confirm or reject only what the user says
     (`uv run fin review confirm <company> <ids> --customers [--sector "..."]`, `reject ... --customers`). Customers
     without a confirmed sector show as `unmapped`: say so. Describe the figures; do not judge the company.
   - **Reclassification.** Revenue and cost of the same size moved out of both (for example a recharge shown gross):
     `--category reclassification --revenue-effect -X --cost-effect -X` (EBITDA effect 0; the margin on adjusted
     revenue changes). A cost moved between two cost accounts: `--category reclassification --account <to>
     --from-account <from> --moved-amount <rupees> --amount 0` (both in `fin map accounts`, expenses of the profit
     and loss; no effect on any total).
   - Categories are `adjustment_categories` in `config.toml`; the period must be one of the engagement's; an
     account must be in `uv run fin map accounts`.
   - Describe what the item is and the evidence; do not judge the company.
7. **Decisions are the user's.** Show the proposed adjustments (`uv run fin adjust list <company> --status
   proposed`) and accept or reject only the ones the user names: `uv run fin adjust accept <company> <ids...>`,
   `uv run fin adjust reject <company> <ids...> --reason "..."`. Accepting or rejecting needs gate G2 approved
   (step 9).
7a. **Recast.** With a sales register loaded, run `uv run fin recast run <company> [--period FY25]`: each line's Amount in Rs is
   spread over its service dates by month and compared with revenue booked by invoice date; the difference by period is proposed
   as a `recast` adjustment (revenue effect = EBITDA effect). Show the output as it is. Sales invoices with no service dates are
   listed as `undated` (recognised on the invoice date): group those at or above the threshold into a draft query with
   `uv run fin query raise <company> --items <ids> --question "..."`; the user approves it. Never fill in service dates. A contract
   is optional evidence only: read it, write a reading CSV (customer token or name, optional invoice_no, start, end, value,
   billing_terms, page) and load it with `uv run fin recast contracts <company> <file> --reading <csv>`; it is shown beside the
   undated line and never changes the schedule. The user accepts or rejects the proposed recast adjustments (step 7).
8. **Bridge and walk.** `uv run fin engagement bridge <company> [--period FY25] [--scope standalone|consolidated]`
   (`--json` for rupees) shows, per period and scope of the latest engagement, the EBITDA bridge (reported EBITDA,
   each accepted adjustment, adjusted EBITDA) and the EBITDA-to-cash walk (non-cash and other, working capital
   change, taxes paid, other operating, CFO, capex, free cash flow, cash conversion). Report the figures from that
   output (or `fin sql` on the views `adjusted_ebitda`, `ebitda_bridge`, `ebitda_to_cash`), and flag:
   - the margin on adjusted revenue (`adjusted_ebitda_margin`, with `adjusted_revenue`) differs from the reported
     margin when accepted adjustments have a revenue effect: show both, labelled;
   - proposed adjustments: shown as "not applied" with their sum; they are not in adjusted EBITDA until the user
     accepts them;
   - a line shown as `-`: its input is missing (named in "missing: ..."); say which, never treat it as zero or fill
     it in;
   - open or accepted findings and unconfirmed mappings in those periods, as for any answer.
   - **Working capital.** `fin sql` on `nwc_year_end` (each year-end and the stub's end, from the trial balances;
     `nwc_year_end_lines` has each ledger's trial balance row) and `nwc_monthly` (month ends from the Ledger Vouchers on the
     previous year-end's closing). The monthly view has only the working capital ledgers whose movements tie to the trial
     balance (`gl_vs_tb`); `nwc_monthly_ledgers` lists each ledger as included or excluded with the reason, and a month with
     `ledgers_excluded` above zero is not the complete working capital: say so. A period with no previous trial balance has
     no monthly view; never infer an opening. The stub is unaudited management accounts.
   - **Diligence report.** `uv run fin report <company> --diligence [--scope standalone|consolidated]`. The first run
     writes `reports/<company>/<scope>/diligence_narrative.md` with one `## ` heading per section (basis and gates, summary,
     adjusted EBITDA, revenue, EBITDA to cash, working capital, reconciliations and queries, collections, stub); the next run
     lints it and renders HTML, Word and PDF (`diligence_data.json` has every value and the gate states it was rendered
     with). Read the figures with `fin sql` and the views above, then write each section describing what the tables show:
     no recommendation, rating or verdict; proposed adjustments are "not applied"; the stub is unaudited management accounts;
     tokens for names. Never type a number: write `{{figure:<name>:<period>}}` (names are listed in the stub, e.g.
     `{{figure:adjusted_ebitda:FY25}}`, `{{figure:net_working_capital:FY26-YTD-SEP}}`); any other digit is E602, an unknown figure or
     period is E874, no engagement is E873. The report also has the recast (once `fin recast run` has run), the collections and
     ageing, the customers section and a sources appendix; they need no narrative. The report renders at any gate state and
     prints each gate's state; tell the user which gates were not approved.
9. **Gates.** Gates are approved in order, by the user only: G1 Ingestion (every period in scope has mapped figures
   from a loaded document, no held documents, no mappings waiting for review; once there is a trial balance or a
   stub, also a trial balance and Ledger Vouchers per period and the stub, every ledger in both, every ledger
   mapped, every bank parse tying (once a bank statement is loaded), and each year's `fin reconcile` on the current data with every difference at or above the threshold in
   a query; once a payroll file is loaded, its unit confirmed by the user), G2 Reconciliation (no query in draft;
   every D4 reconciliation whose inputs are loaded ran on the current data and every line at or above the threshold
   is in a query, fx lines excepted; unlocks `fin adjust accept|reject`), G3 Recast (once a sales register is loaded, for every annual period: `fin recast run` ran on the current
   data, every undated sales invoice at or above the threshold is in a query, the period's recast difference has a proposed or decided
   `recast` adjustment; unlocks approving G4), G4 Adjustments (no proposed adjustment, every accepted one has evidence;
   unlocks `fin engagement close`; an engagement with no queries and no adjustments closes without its gates). A
   gate reopens on its own when the data under it changes (G1 also when a figure changes) or its checklist fails.
   - Run `uv run fin gate status <company>` and show each gate's state, its checklist lines and what it unlocks.
   - Fix what a failing line says through the usual commands: held documents and mappings through `/ingest` and
     `/review` (the user decides findings and mappings), drafts through step 3, proposed adjustments through
     step 7. Run `fin gate status` again.
   - When the checklist passes, ask the user to approve the gate. Run `uv run fin gate approve <company> <gate>`
     only when the user says so; a hook asks the user before any command that looks like a gate approval, and
     `fin sql --write` cannot write gate approvals (E806). E802 means the checklist fails or an earlier
     gate is not approved: show the failing lines.
   - A command refused with E801 names the gate and its state (open or reopened, and what changed): show it and
     go back to `fin gate status`.
10. **Never** approve, mark sent, close or withdraw a query, accept or reject an adjustment, or approve a gate on your
   own. Never change the company's data to make a difference go away; fixing fin's own reading (a wrong mapping, a
   misread figure) is still done through `/ingest` and `/review`. If a command fails (E801 a gate not approved, E802
   a gate not ready, E803 no open engagement, E804 an adjustment that is not valid, E805 an unknown id or a status
   change not allowed, E806 `fin sql --write` on a diligence table, E807-E811 a source file that cannot load or
   `mask_values` read, E812 a reconciliation that cannot run, E845 a pass-through customer that is not on a loaded sales register, E820-E821 payroll: no loaded payroll file named, or an unreadable row, E861 a token that is not the company's, E881 a query period outside the engagement), report the error code and run_id and stop.
