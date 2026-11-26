# FDD extension — plan

Owner decision, 2026-11-25 ("whatever u can build you should build including fdd style findings report"): build
what fin can do of the diligence user's FDD gap list (`docs/diligence.md` §10), including an FDD-style findings
report. This file is the plan; each workstream updates its own section to "built" and records its product-level
choices in `docs/decisions/fdd-<id>.md`. Scope readings that are the orchestrator's, not the owner's, are in
[decisions/fdd-scope.md](decisions/fdd-scope.md).

## Rules (apply to every workstream)

- The diligence rules hold (`CLAUDE.md`, "Diligence"): differences are queried, never fixed; Claude drafts, the user
  decides; fin never sends anything; the client's fields (register parties, payroll names) are tokens in everything fin prints
  (`docs/decisions/masking.md`).
- fin computes the repeatable parts; judgement stays with Claude (proposals) and the user (decisions). Every
  proposal Claude makes is reviewed by the user through `fin review` or an accept/reject command, as for ledger
  mappings and adjustments.
- Claude's readings of documents (contracts, notes to accounts, notices) load from a reading CSV with file + page per
  row, `read_by = claude`, as `fin recast contracts` does. fin does not check them; trusting one is the user's call.
- A difference at or above the engagement threshold is a recon item Claude can group into a query
  (`fin query raise --items`); below it is listed. New checks reuse `recon_runs`/`recon_items` where they compare
  two sources.
- Indicative figures (computed on a stated assumption) are labelled "indicative" with the assumption wherever shown,
  and the assumption is a `config.toml [fdd]` key.
- New tables in their own schema file `db/schema/fdd_<area>.sql`; no migrations. Error codes in the workstream's
  range below. Diligence tables still change only through fin commands (`fin sql --write` refuses them, E806).
- Each workstream adds: `docs/product.md` section, errors to `docs/errors.md`, synthetic fixtures, unit and
  integration tests, a golden eval kind for each new figure a person can check, and a dashboard/report data
  function its section in F8 can call.

## Workstreams

| id | Gaps | Errors | Depends on |
|---|---|---|---|
| f1 contracts | 1, 2 | E901–E909 | — |
| f2 inquiries | 3 | E910–E919 | — |
| f3 bsheet | 4, 7 (provisions, cut-off) | E920–E929 | — |
| f4 netdebt | 7 (net debt, debt-like) | E930–E939 | — |
| f5 disclosures | 5, 6 (notices), 8 | E940–E949 | — |
| f6 gst | 6 | E950–E959 | — |
| f7 orderbook | 9 | E960–E969 | — |
| f8 fddreport | 10 | E970–E979 | f1–f7 merged |

### F1: Contract terms and deal walkthroughs (gaps 1, 2) — built

Built as below; commands and views in `docs/product.md` ("FDD contracts and walkthroughs"), choices in
[decisions/fdd-f1.md](decisions/fdd-f1.md). Commands: `fin recast contracts` (extended, loads customer and vendor
contracts with terms), `fin contracts coverage`, `fin sample draw|show`, `fin walkthrough record|summary`. Views:
`contract_coverage`, `walkthrough_summary`. Data functions for F8: `fin.reconcile.contract_coverage.coverage`,
`fin.sampling.walkthrough.summaries`. Errors E901–E903 (the contracts loader keeps E864–E865).

- Contract readings gain terms: party kind (customer/vendor), counterparty (token), start, end, value, lock-in
  months, notice period days, SLA terms, penalty terms, escalation terms and escalation % a year, renewal terms,
  auto-renew, termination for convenience, change of control, exclusivity; each with file + page. Vendor contracts
  load the same way. Extend `contracts` and `fin recast contracts` (or one `fin contracts load`, the builder's call).
- View `contract_coverage`: the top customers by core revenue and top vendors by purchases (count from
  `[fdd] coverage_top_n`, default 10) for the latest period: contract on file or not, expiry within 12 months after
  the last period end, auto-renew, change-of-control clause, lock-in left.
- `fin sample draw <co> --kind sales|purchase --period P [--n 25]`: every line at or above the threshold (largest
  first, up to n), then the rest drawn at random with a seed stored on the sample, so a draw is reproducible. Table
  `samples`.
- `fin walkthrough record <co> <sample-id> <file.json>`: Claude's walkthrough of a sample line, one result per check
  (pass / exception / not available) with evidence: the line is in the register; a contract covers it and its rate,
  dates and value agree; the service period sits inside the contract; a bank receipt is matched (D5); the ledger
  posting is found (D2). Table `walkthroughs`; view `walkthrough_summary` (per sample: lines, checks passed,
  exceptions). An exception at or above the threshold is a recon item for a query.

### F2: Management inquiries (gap 3) — built

Decisions: [decisions/fdd-f2.md](decisions/fdd-f2.md). Schema: `db/schema/fdd_inquiries.sql` (table `inquiries`). Error E910.
Commands: `fin query raise --topic`, `fin query checklist <co> [--json]`. Data function for F8:
`fin.diligence.inquiries.collect(con, engagement_id, topics)` (per topic: its queries, status, answers).

- A query can be an inquiry: `fin query raise <co> --topic <topic> --question "..."` with no amount or source. Topics
  from `[fdd] inquiry_topics`: revenue_recognition, cut_off, mis_kpi, accounting_policies, capitalisation,
  provisions, related_parties, tax, contingencies, other.
- `templates/fdd/inquiries.toml`: a standard FDD inquiry checklist per topic that Claude adapts to the company
  (it drafts; the user approves, as for every query). `fin query checklist <co>` shows each topic with its queries and
  their status, and the topics with none.
- Inquiries follow the query life cycle (approve, sent, answer, close) and appear in `fin query export` (email and
  brief) and in G4 like any query. An answer file is archived as now.

### F3: Provisions, payroll liabilities and cut-off (gaps 4, 7) — built

Decisions: [decisions/fdd-f3.md](decisions/fdd-f3.md). Schema: `db/schema/fdd_bsheet.sql`. Errors E920–E923.

- Provision ledgers: the dictionary has no account for the provision for doubtful debts or the gratuity provision,
  so Claude proposes the trial balance ledgers that hold them (`fin provisions propose <co> <file.json>`:
  `[{ledger, kind: doubtful_debts|gratuity, reason}]`) and the user confirms or rejects them
  (`fin provisions confirm|reject <co> <ids>`; `fin provisions ledgers <co>` lists them). Table
  `provision_ledgers`; `fin sql --write` refuses it (E806). View `provision_balances`: per period with a loaded
  trial balance and kind, the confirmed ledgers' credit less debit.
- `receivable_provision` (view): at each year end of the latest engagement with a `bank_vs_invoices` run, the sales
  invoices open at that date (from the latest run per period: unmatched, or the part of a match group's invoices
  not covered by its receipts dated by the year end, so instalments count) aged 0–90, 91–180, 181–365 and over 365 days from invoice date, with each
  bucket's share, the doubtful debts provision, its coverage of the bucket and every older one, and the invoices in no
  run (not aged). The lines are the view `receivable_open_lines`.
- `gratuity_indicative(basic_share, min_years, cap)` (table macro; per employee `gratuity_lines`): per payroll
  snapshot whose unit the user confirmed annual, employees with at least `[fdd] gratuity_min_years` completed years:
  15/26 × monthly wages × years, capped at `[fdd] gratuity_cap`; wages = gross salary × `[fdd] basic_share`, and a
  wage-code figure with wages at least 50% of CTC; against the gratuity provision, with the shortfall. Labelled
  "indicative, undiscounted, not an actuarial valuation" with the assumption. Leave encashment: not built.
- `fin provisions show <co> [--period P] [--json]` prints both; `fin.diligence.provisions.collect(con, fdd)` is the
  data function the FDD report (F8) calls.
- Cut-off as reconciliations (`fin/reconcile/cutoff.py`, in `RECONCILIATIONS`, so `fin reconcile <co>` and gate G2
  include them): `fin reconcile <co> sales_cutoff`: sales invoices within `[fdd] cutoff_days` (15) of a year end
  whose service is on the other side (billed ahead: service starts after it; billed late: service ended by it);
  `purchase_cutoff`: purchase invoices within `[fdd] unrecorded_days` (60) after a year end for a service that ended
  by it (both windows copied onto the engagement when it opens). A service that straddles the year end is its own
  line for the part up to the year end (by days, in SQL); invoiced after the year end for a service started by it with no service end: flagged in full, the note says so.
  One line per invoice (left_only, level invoice); at or
  above the threshold it needs a query (`fin query raise --items`). Lines with no service start, and registers that
  end before the window, are listed as not reconciled.
- Golden kinds: `receivable_ageing`, `doubtful_debt_provision`, `gratuity_provision` (`evals/provisions.py`),
  `sales_cutoff`, `purchase_cutoff` (recon kinds).

### F4: Net debt and debt-like items (gap 7) — built

Decisions: [decisions/fdd-f4.md](decisions/fdd-f4.md). Schema: `db/schema/fdd_netdebt.sql`. Errors E930–E934.

- Each trial balance ledger of the latest engagement has a net debt class: cash, debt, debt_like or none, one class
  per ledger for every period end. fin defaults it from the ledger's confirmed account (`netdebt_default_accounts`:
  borrowings, debt securities, subordinated liabilities and lease liabilities are debt; cash and cash and bank
  balances are cash; a profit and loss account is none) and leaves the rest unclassified, other bank balances
  included
  (`netdebt_ledger_classes`). Claude proposes classes (`fin netdebt propose <co> <file.json>`:
  `[{ledger, class, reason}]`, E930; without a file it lists the unclassified ledgers) and the user confirms or
  rejects them (`fin review list|confirm|reject <co> [ids] --netdebt`, E931). A confirmed class wins over the
  default; rejecting it restores the default. Table `netdebt_classes`.
- Items not in a ledger: `fin netdebt item propose <co> --period P --amount N --description ... --evidence <ref>`
  (signed rupees, positive raises net debt; a year of the open engagement or its stub; text as written; E932); the user
  accepts or rejects them (`fin netdebt item accept <co> <ids>`, `reject <co> <ids> --reason`, E933;
  `fin netdebt item list <co>`). Table `netdebt_items`.
- Views: `net_debt` per period end of the latest engagement (annual periods, then the stub as unaudited): cash, debt,
  debt-like (ledgers and accepted items), net debt = debt + debt-like − cash, proposed items (sum and count, not
  applied), unclassified ledgers and classes waiting; ledger figures NULL for a period with no trial balance.
  `net_debt_lines`: each ledger (file + sheet!row) and item (its evidence), applied or not.
  `fin netdebt show <co> [--period P] [--json]` (E934 without an engagement).
- `fin sql --write` refuses `netdebt_classes` and `netdebt_items` (E806); each change is a run event. Golden kind
  `net_debt` (`ledger`: cash, debt, debt_like, net_debt). The FDD report (F8) reads F4 through
  `fin.diligence.netdebt.collect(con, period)`.

### F5: Contingencies, commitments, notices and related parties (gaps 5, 6, 8) — built

Built (decisions: [decisions/fdd-f5.md](decisions/fdd-f5.md); module `fin/disclosures/`, schema
`db/schema/fdd_disclosures.sql`, errors E940–E941):

- `fin disclosures load <co> <file> --reading <csv>`: Claude's reading of the notes to accounts, legal and tax
  notices, one row per item: kind (contingent_liability, commitment, guarantee, litigation, tax_matter,
  off_balance_sheet, related_party, related_party_transaction, related_party_balance), period, counterparty (masked
  on load), nature, amount and `unit` (rupees, thousand, lakh, crore, million), forum and stage, assessment year,
  demand, amount paid under protest, relationship, page; for a related party transaction, `books` (sales,
  purchases, debit, credit) and `ledger` say what it is compared with. Tables `disclosure_files`, `disclosures`.
  `fin disclosures list <co> [--kind K] [--json]`: the schedules, one per kind, each row with file + page, "not
  verified".
- Related parties: a counterparty that is a register customer or vendor gets its token; one that is a loaded
  ledger's name becomes that ledger (case and spaces ignored); fin matches it to the customers and vendors by token
  and to the ledgers by name, a ledger meeting a token through `mask_values` (view `related_party_matches`, status
  `matched`). `fin disclosures related <co>` lists each party and its matches; `fin disclosures related <co>
  <file.json>` stores Claude's link proposals (a party by token or name; `{party, kind: customer|vendor|ledger, target, confidence,
  reason}`, table `related_party_links`); the user confirms or rejects them (`fin review list|confirm|reject <co>
  --related`).
- Views: `related_party_transactions` (per party and period: the linked customers' sales register, vendors' purchase
  register and linked ledgers' Ledger Vouchers entries per voucher type), `related_party_pricing` (rate per product
  against the other customers or vendors, difference %), `related_party_loans` (balances at each year end, average,
  interest booked, implied rate). `fin reconcile <co> rpt_vs_books` compares the disclosed transactions with the
  books (level `related_party` lines; at or above the threshold for queries; not_reconciled when the books side
  cannot be read); it is in `RECONCILIATIONS`, so gate G2 requires it once a reading has a transaction to compare.
- Golden eval kind `rpt_vs_books`. Report data: `fin.disclosures.data.section_data(con, engagement_id)`.

### F6: GST and income tax (gap 6)

**Built.** Commands: `fin ingest <co> <file> --kind gst --profile gstr1|gstr3b` (stored as `gstr1`/`gstr3b` source
files, table `gst_returns`), `fin map tax <co> [file.json]` and `fin review list|confirm|reject <co> ... --tax
[--tax-class]` (table `tax_ledgers`), `fin reconcile <co> gstr1_vs_sales_register|gstr3b_vs_gstr1|gstr3b_vs_books|rcm`
(`recon_runs`/`recon_items`, level `gst`, required by G2 once their inputs are loaded). Views: `tax_charge`,
`tax_ledger_balances` (db/schema/fdd_gst.sql). Golden eval kinds: `gst_return` and the four reconciliations. F8 data
function: `fin.fdd.tax_data.collect(con)`. Errors E950-E951. Decisions: [decisions/fdd-f6.md](decisions/fdd-f6.md).
Plan as written:

- `fin ingest <co> <file> --kind gst --profile gstr1|gstr3b`: monthly return summaries (taxable value, IGST, CGST,
  SGST, cess; for GSTR-3B also input tax credit, reverse-charge liability and tax paid in cash and credit), with file +
  sheet!row. Synthetic profiles in `templates/profiles/`.
- Tax ledgers: Claude proposes which trial balance ledgers are GST output, GST input, RCM, TDS, income tax; the user
  confirms (`fin review ... --tax`).
- Reconciliations: `gstr1_vs_sales_register` (taxable value by month), `gstr3b_vs_gstr1` (outward supplies by month),
  `gstr3b_vs_books` (output tax and ITC against the tax ledgers by month), `rcm` (RCM liability in GSTR-3B against
  the RCM ledger).
- `tax_charge` view from the statements: profit before tax, current tax, deferred tax, effective tax rate, and the
  deferred tax asset or liability movement per year; MAT credit where mapped.
- Notices and assessments come in through F5's `tax_matter` rows.

### F7: Orderbook and revenue visibility (gap 9) — built

Choices in [decisions/fdd-f7.md](decisions/fdd-f7.md); behaviour in `docs/product.md` ("FDD orderbook and revenue visibility").
Schema `db/schema/fdd_orderbook.sql`; errors E960–E961. Commands: `fin ingest <co> <file> --kind orderbook` (profile
`orderbook`), `fin visibility <co> [--json]`, `fin reconcile <co> orderbook_vs_register`. Views: `orderbook_current`,
`visibility_basis`, `backlog_lines`, `revenue_visibility`. Data function for F8: `fin.reconcile.visibility.visibility`. Golden kinds
`backlog_remaining`, `visibility_covered`.

- Orderbook rows: customer (token), order number and date, value, currency, start, end, billed to date, as at a date; file + sheet!row.
  An order whose term ended by the as-at date adds nothing; the as-at date is shown, with a note when it is not the period end.
- Contracted backlog, per source and never added: unbilled orderbook value; revenue billed but not yet earned at the period end (service-period
  recast, D6); customer contract value remaining after the period end (F1).
- `revenue_visibility`: for the year after the last period end, the part of each source falling in it (straight-line over the remaining term),
  against the last year's core revenue.
- `orderbook_vs_register`: orders with no billing, billing with no order (matched by customer: the register has no order number).

### F8: FDD findings report (gap 10) — built

Built as below; behaviour in `docs/product.md` ("FDD findings report"), choices in
[decisions/fdd-f8.md](decisions/fdd-f8.md). Schema `db/schema/fdd_findings.sql` (table `fdd_findings`, view
`fdd_finding_rows`); errors E970–E972. Commands: `fin fdd finding propose|update|list|drop <co>` (Claude),
`fin fdd finding final <co> <ids> [--undo]` (the user's word only; the gate-guard hook asks first),
`fin report <co> --fdd [--drafts] [--render]` (template `templates/report/fdd.qmd`, files `fdd_narrative.md`,
`fdd.html|docx|pdf`, `fdd_data.json`). Data function: `fin.fdd.findings.collect(con, engagement_id, drafts)`;
the report reads every workstream's data function through `fin.report.fdd_data.collect`. Plan as written:

- Table `fdd_findings`: area, title, observation, impact type (EBITDA, net debt, working capital, revenue,
  disclosure, none), amount (only from linked evidence: an adjustment, a net debt item, a recon run or a query),
  severity (high, medium, low), deal consideration (price, SPA indemnity or warranty, condition precedent, further
  work, none), management response (from the linked query's answer), status (draft, final), evidence refs.
  A final finding shows the amount, source and response snapshotted when it was made final, flagged "changed since
  final" when the live evidence differs; a rejected linked adjustment or item, or a withdrawn query, is flagged.
  `fin fdd finding propose|list|update|drop <co>` (Claude); `fin fdd finding final <co> <id>` (the user's word only).
- `fin report <co> --fdd [--render]`: key findings by severity; quality of earnings (D1, D7); working capital; net
  debt and debt-like items (F4); revenue, contracts and visibility (F1, F7, D9); walkthroughs (F1); provisions and
  payroll (F3); tax (F6); contingencies and commitments (F5); related parties (F5); inquiries and their answers
  (F2); gate states; scope and limitations (what fin did not do: actuarial valuation, legal review, site visits);
  sources appendix. A section whose data is not loaded says so. Review fix (owner's review, 2026-11-26): the report
  also shows what F1, F5 and F6 collect: contract lock-in left, notice period and auto-renew, walkthrough exception
  lines, related party pricing and loans, deferred tax movement and MAT credit, and the GST returns' totals
  ([decisions/fdd-review-fixes.md](decisions/fdd-review-fixes.md)).
- Narrative rule for this report only (owner, 2026-11-25): observations, severity, implications and deal
  considerations are allowed; no valuation, rating of the company as an investment, or buy/don't-buy language.
  Numbers only as references, as in the other reports. Only final findings appear unless `--drafts`.
