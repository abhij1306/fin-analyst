# Client handoff: run fin on your documents and report back

For the Claude Code agent on the client's Mac. It assumes no prior context. The goal: install fin, load the client's
real documents, build a **golden set** (values a person checked in the documents), score fin against it, ask the
client to confirm fin's product decisions, and prepare a **results report** for fin's maintainers.

Read `CLAUDE.md` first. The rules that matter here:

- Use `uv run fin ...` for all data access. Every figure you state comes from `fin` output, except golden values,
  which come from the documents.
- **Claude does not verify its own reading.** A golden value is scored only once a person has checked it against the
  document. You draft; only the person marks a line checked.
- Data never leaves the Mac except the results report and bundle, and only when the person sends them. The data
  folder (`~/FinAnalysis`), golden sets and eval reports hold real figures and file names, and `mask_values` holds
  real names: never paste them into a message, an issue or a PR.
- If a command fails, report its error code and run_id (the `summary:` line) and stop that step. Don't work around
  it. Codes: [errors.md](errors.md).

## 1. Install

```sh
git clone https://github.com/abhij1306/fin-analyst.git && cd fin-analyst
./install.sh            # Homebrew tools, uv sync, the data folder ~/FinAnalysis, fin init, fin doctor
uv run fin doctor       # every line ok or warn; a FAIL shows its fix
```

Another data folder: `export FIN_DATA_DIR=...` before `./install.sh` (and in `~/.zshrc`). The optional PDF add-on
(README, "Install"): `./install.sh --parser docling` (local) is the usual choice; LlamaParse uploads pages to a
cloud service, so only if the client says so. Keep the `fin doctor` result: the bundle includes a summary.

## 2. Load the documents

1. Statements (Excel, PDF) in `~/FinAnalysis/inbox/<company>/` (lower-case company names), then `/ingest <company>`.
   **Ask; don't decide:** mappings are confirmed (`/review`), findings saved as is (`fin findings accept`) or fixed,
   only when the person says so. Pages fin could not read are read by Claude only when the person asks
   (`fin ingest --reading`).
2. Diligence sources, when the client has them (`/diligence <company>`; skip for statements only): open an engagement
   (ask which years, and whether there is a current-year stub); per period the Tally trial balance (Detailed mode)
   first, then its Ledger Vouchers; then registers, bank statements (a PDF needs the add-on) and payroll (ask whether
   amounts are annual or monthly: `fin payroll confirm-unit`). Map ledgers (`fin map ledgers`; the person
   confirms), then `fin reconcile <company>`. A duplicate invoice, a statement that does not tie, an overlap or a
   gap is a finding: show it, never fix it. E807 means no column profile reads the layout: write one
   ([development.md](development.md), "Add a column profile") and list it in the report.
3. Names are tokens (`CUST-0001`, ...). Use the tokens; never try to read the real names (`fin sql` refuses, E810).

## 3. Build the golden set

`~/FinAnalysis/evals/<company>/golden.csv` is never committed and never shared. `uv run fin eval init <company>`
writes it (and adds new lines after more is loaded; it never changes a line already there): one line per output
fin has, with its `source`, `expected` and `checked` empty. Columns: `kind`, `period`, `scope`, `account`, `ledger`,
`expected` (as printed: `1,234.56`, `(56.67)`, `-` for nil), `unit` (`rupees`, `thousand`, `lakh`, `million`,
`crore`), `source` (`file sheet!cell`, `file sheet!row` or `file page N`), `checked` (`yes`), `note`.

**Draft from the documents, never from fin.** Open the file at `source` (Read: Excel cells, or the PDF page as an
image) and copy the figure as printed, with its unit. Don't run `fin sql`, `fin statements` or `fin audit` for these
lines first. If `source` points at the wrong place, find the figure and correct `source`. Then give the person the
drafted lines (period, account or ledger, expected, unit, source); they compare with the document, and you write
`yes` only on lines they say they checked.

### What to check, in this order

Each step says what a wrong result looks like. Stop at the first step with failures worth understanding; a later
step builds on the earlier ones.

| # | Kinds | How many | What it catches |
|---|---|---|---|
| 1 | `statement` | about 30 per company: every statement, both scopes, every year (quarters if loaded); totals and the lines under them; PDF and Excel; Claude-read lines; every line whose note says fin adds several printed lines into one account | layouts and units (lakh vs crore, a unit only in a footnote); a line missing or under the wrong label; wrong total chosen when several lines map to one account |
| 2 | `adjusted_ebitda` for a period with no accepted adjustment | one per period | EBITDA blank or wrong when the statement lacks a finance cost or depreciation line |
| 3 | `tb_ledger`, `tb_vs_statements`, `stub` | some party and other ledgers; every difference line; the stub lines | Tally export variants (groups, opening rows); ledgers missing or doubled; a cash ledger mapped to the wrong account (large equal and opposite drafts) |
| 4 | `bank_closing`, `register_total`, `payroll_total`, `gst_return` | one per statement, per register sheet, per payroll file | rows dropped between PDF pages; credit notes, GST or foreign-currency columns; monthly payroll amounts (the unit answer) |
| 5 | `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`, `gstr1_vs_sales_register`, `gstr3b_vs_gstr1`, `gstr3b_vs_books`, `rcm` | each detail line you can work out | what the registers, books and payroll disagree on, and whether fin found exactly that |
| 6 | `bank_unmatched`, `invoice_unmatched`, `fx_line`, `customer_received`, `customer_partly_paid`, `bank_vs_tb` | per bank account and customer with receipts; every fx line | receipts matched to the wrong invoice; a false fx gain; "no booked value" where the books do hold the receipt (see question 3) |
| 7 | `recast_revenue`, `recast_difference`, `recast_undated`, `core_revenue`, `passthrough_revenue`, `adjusted_revenue` | per period | service dates written in words or in a remarks column (every line undated); months that do not add to the line; pass-through list |
| 8 | `query_issue`, `query_difference` | each approved query | a query that misses its period or amount, or absent from the lists and gates |
| 9 | `customer_revenue`, `sector_revenue`, `product_revenue`, `top_customers_revenue`, `retained_revenue`, `pvm_*`, `nwc_year_end`, `nwc_monthly` | a few per period | analytics and working capital on real data |
| 10 | `receivable_ageing`, `doubtful_debt_provision`, `gratuity_provision`, `sales_cutoff`, `purchase_cutoff` | per year end; each flagged cut-off invoice | an invoice aged in the wrong bucket or counted open when it was paid; the wrong provision ledgers confirmed; a cut-off line on an invoice whose service dates are read wrongly |
| 11 | `backlog_remaining`, `visibility_covered` | per backlog source with items | orderbook headers or currencies read wrongly; orders with no end date; a contract value not read |
| 12 | `net_debt` | per period end with a trial balance | a cash, debt or debt-like ledger in the wrong class (check the confirmed classes); an overdraft or a debit balance read with the wrong sign |

What each kind's value is (the person works out the derived ones from the documents; rupees unless said):

| Kind | Value |
|---|---|
| `statement` | the line as printed, with its sign |
| `tb_ledger` | closing debit less closing credit (debit positive) |
| `tb_vs_statements` | trial balance ledgers mapped to the account, rolled up, less the audited standalone figure |
| `stub` | stub trial balance ledgers mapped to the account, as the statements show it (income and liabilities positive) |
| `bank_closing` | the closing balance printed on the statement, in the account's currency, tying or not (`ledger` is the account number; `period` the statement's end date) |
| `register_total` | Amount in Rs (before GST) summed over every invoice row of the sheet, total row left out, duplicates counted (`ledger` is `sales` or `purchase`) |
| `payroll_total` | the gross salary or CTC column summed over the snapshot's employees, annual rupees as in the file (`ledger` is `gross_salary` or `ctc`) |
| `gst_return` | a GST return month's figure as filed (`ledger` is the return, month and field: `gstr1 2024-05 taxable_value`, `gstr3b 2024-05 itc`) |
| the GST reconciliation kinds | the line's difference, the return less the other side: GSTR-1 taxable value less the sales register's Amount in Rs for the invoice month; GSTR-3B less GSTR-1; GSTR-3B output tax less the credits on the output tax ledgers (ITC less the debits on the input ledgers; RCM liability less the credits on the RCM ledgers) in the month. `ledger` is the month and measure, `2024-05 output_tax` |
| the four reconciliation kinds | the line's difference: register less books (an invoice's Amount in Rs less the Ledger Vouchers amount on the revenue or purchase ledgers), Ledger Vouchers movement less trial balance change, payroll CTC less employee expense; `ledger` is the invoice number, the ledger, or the period |
| `bank_unmatched`, `invoice_unmatched`, `fx_line` | receipts (credits) no invoice matched, in the account's currency; the customer's invoices with no receipt; the customer's fx gain (positive) or loss (receipt as booked in Ledger Vouchers less the invoice's Amount in Rs), 0 if none. `bank_vs_tb`: bank closing balances less the trial balance's cash and bank ledgers. `customer_received`: the customer's invoices paid in full (Amount in Rs); `customer_partly_paid`: the customer's invoices whose receipts differ from them (the invoice Amount in Rs) |
| `core_revenue`, `passthrough_revenue` | sales register Amount in Rs for invoices dated in the period, customers not on / on the pass-through list |
| `recast_revenue`, `recast_difference`, `recast_undated` | Amount in Rs spread by service day over months, recognised in the period; the same less invoices dated in the period; invoices with no service dates |
| `adjusted_revenue`, `adjusted_ebitda` | the statement's revenue (EBITDA) plus the revenue (EBITDA) effects of accepted adjustments |
| `query_issue`, `query_difference` | an issue's amount at issue, differences added without signs (`ledger` is the query id; for a difference, the id, a space, and `left_only`, `right_only` or `difference`) |
| the analytics kinds | on the sales register, core customers: revenue of a customer token / of a confirmed sector / of a product; the top 1, 5 or 10; revenue retained from last year's customers; price, volume and mix effects of the change in a product's revenue |
| `nwc_year_end`, `nwc_monthly` | working capital from the trial balance; the month's, from Ledger Vouchers, for ledgers that tie |
| `receivable_ageing` | sales invoices open at the year end (unpaid, or paid only after it; the unpaid part of a partly paid one), Amount in Rs, in the bucket of days from invoice date to the year end (`ledger`: `0-90`, `91-180`, `181-365`, `over 365` or `total`) |
| `doubtful_debt_provision`, `gratuity_provision` | the closing credit less debit of the ledgers the user confirmed as that provision, in the period's trial balance |
| `sales_cutoff`, `purchase_cutoff` | the Amount in Rs of one invoice the cut-off test flagged at the period's year end (`ledger` is the invoice number) |
| `backlog_remaining`, `visibility_covered` | for a backlog source (`ledger`: `orderbook`, `billed_not_earned` or `contract`) at the last annual period: what is still to come (orderbook: value less billed to date of INR orders; billed not earned: register amount for service days after the period end; contract: value for days after the period end), and the part in the 12 months after the period end, both straight-line by day |
| `net_debt` | at the period end, from its trial balance: cash (cash and bank ledgers, debit less credit), debt (borrowings and lease liabilities, credit less debit), debt-like (the ledgers confirmed debt-like, credit less debit, plus the accepted items) or net debt (debt + debt-like − cash); `ledger` is `cash`, `debt`, `debt_like` or `net_debt` |

## 4. Run the eval

`uv run fin eval run <company>` prints the score and the report path (`evals/<company>/eval-<run_id>.md` and `.json`).
Each checked line is compared exactly in rupees after unit conversion: `pass`, `fail`, `missing` (fin has no value),
`unchecked` (not scored). The report also counts error codes, findings, unread pages, Claude-read figures, unmapped
lines and ledgers.

Show the person the score and each line that does not pass, with both values and the source. A failure is a question:
- fin misread a figure or mapped it wrongly: fin's error. Fix it the usual way (`/review`, `fin map set`,
  `fin ingest --force`) only when the person says so, and keep the report from before the fix as evidence.
- The golden value is wrong: correct it with the person and run again.
- The document does not add up: a finding (`fin findings list`), not an eval failure.

**A `fail` on a figure fin loaded with no warning is the most important thing the eval can show.**

## 5. Ask the client to decide

[Decisions](decisions/) record how fin's builders read the plan where it left a choice. They are readings, not the
client's decisions. Ask the client about each below: keep it, or change it (and what to). Show each in plain words
(the Decision and Why of the entry), with an example from their own data where the eval shows one. Record the
answer with the date, in the client's words. Do not change fin; the maintainers do.

1. **Masking scope** ([masking.md](decisions/masking.md), marked NEEDS CLIENT CONFIRMATION). Ask first: it
   decides what Claude sees.
2. **Booked receipts for foreign-currency or part-paid invoices** ([test-run.md](decisions/test-run.md), 10% band;
   [d5-bank-match.md](decisions/d5-bank-match.md) 5). Several receipts paid against one invoice with a single combined
   Receipt voucher show as "no booked value" today (a new matching rule was declined). Ask whether their books do
   this, how often, and how they want it shown.
3. **What counts as the books' revenue, purchase and cash side** ([d4-recon.md](decisions/d4-recon.md) 2, 3; and which
   ledgers are working capital, [d7-qoe.md](decisions/d7-qoe.md) 1).
4. **Matching windows and thresholds** (`config.toml` `[diligence]`: `query_threshold`, `match_window_days`,
   `fx_window_days`, `invoice_due_days`; [d5-bank-match.md](decisions/d5-bank-match.md) 4, 6).
5. **Payroll "looks monthly"** (median gross or CTC under Rs 1,50,000) and that fin never converts
   ([d3-payroll.md](decisions/d3-payroll.md) 4, 5).
6. **A duplicate invoice** (sales: the number; purchases: vendor and number) and a register replacing the earlier one
   ([d3-registers.md](decisions/d3-registers.md) 2, 4).
7. **Service-period recast**: straight line by service day; a line without dates recognised on its invoice date;
   annual periods only ([d6-recast.md](decisions/d6-recast.md) 2 to 4).
8. **Pass-through revenue and its linked purchase cost** ([d4-passthrough.md](decisions/d4-passthrough.md) 1 to 3).
9. **Customer retention, cohorts and price/volume/mix** ([d9-analytics.md](decisions/d9-analytics.md) 3 to 5).
10. **Profit rolled into other equity in `tb_vs_statements`** ([d2.md](decisions/d2.md), "Current-year profit...")
    and **which line is the total when several map to one account** (two lines: a total only when labelled "Total"
    or the other sits under it; three or more: the line equal to the sum of the others). Report how many
    `statement` lines carried the "adds N printed lines" note, and how many passed and failed.
11. **Audit findings, items D-1 to D-10, D-13 and D-14** ([final-fixes.md](decisions/final-fixes.md), "Client
    decisions"; each marked NEEDS CLIENT CONFIRMATION, with its options): D-1 the first period's recast has no
    opening deferral; D-2 `bank_vs_tb` includes cash in hand and deposits; D-3 an omitted nil line makes EBITDA and
    debt NULL; D-4 the 1 lakh validation tolerance; D-5 below-threshold differences that never roll up; D-6 lakh/crore
    digit grouping; D-7 exact full-name masking; D-8 typed free text stored unmasked; D-9 bank account numbers in full;
    D-10 which Tally groups are party groups (the trial balance load lists the groups it treated as party groups: show
    them); D-13 permission mode and allow rules for this agent; D-14 the PDF layout heuristics.

Skip the decisions the eval and their documents do not touch. The gate-reopening question in
[test-run.md](decisions/test-run.md) ("Open for the owner") is for fin's owner, not the client.

## 6. Prepare the results report

Write `~/FinAnalysis/evals/results-report.md`, then the bundle. No names, no file names, no figures (tokens and
accounts only):

- **Client decisions:** per decision above, the file and title, keep or change, what to, the client's words and date.
- **Issues:** one entry per problem, with: *What failed* (the check kind, period, scope, account or ledger token,
  outcome, error codes); *Where* (the kind of source: Excel cell, PDF page, bank PDF, Tally export; never the file
  name); *What the client decided* (fin's error to fix, a wrong golden value, a finding the client accepts, or a
  decision above); *Status* (fixed locally and how, or open). Include layouts fin could not read, column profiles
  you had to write, and any step of this handoff that did not work.
- **Name check:** a line saying whether you saw a real name in any `fin` output or in the bundle (E833 refuses one
  in the bundle).

```sh
uv run fin eval bundle                  # every company; or: uv run fin eval bundle <company>
uv run fin eval bundle --with-figures   # only if the person agrees to share figures
```

fin prints the two paths (`evals/bundle-<run_id>.md` and `.json`): fin version and commit, a `fin doctor` summary,
and per company (`company-1`, ...) the score, every line not passing and the counts. Show the person both files and
the results report, and read them yourself for anything identifying. The person sends the three files to fin's
maintainers. You never send anything.
