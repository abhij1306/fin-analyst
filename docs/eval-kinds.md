# Golden eval: what to check and what each kind's value is

Reference for building a golden set ([client-handoff.md](client-handoff.md), step 3). `fin eval init <company>`
writes `~/FinAnalysis/evals/<company>/golden.csv`. Columns: `kind`, `period`, `scope`, `account`, `ledger`,
`expected` (as printed: `1,234.56`, `(56.67)`, `-` for nil), `unit` (`rupees`, `thousand`, `lakh`, `million`,
`crore`), `source` (`file sheet!cell`, `file sheet!row` or `file page N`), `checked` (`yes`), `note`.

## What to check, in this order

Each step says what a wrong result looks like. Stop at the first step with failures worth understanding; a later
step builds on the earlier ones.

| # | Kinds | How many | What it catches |
|---|---|---|---|
| 1 | `statement` | about 30 per company: every statement, both scopes, every year (quarters if loaded); totals and the lines under them; PDF and Excel; Claude-read lines; every line whose note says fin adds several printed lines into one account | layouts and units (lakh vs crore, a unit only in a footnote); a line missing or under the wrong label; wrong total chosen when several lines map to one account |
| 2 | `adjusted_ebitda` for a period with no accepted adjustment | one per period | EBITDA blank or wrong when the statement lacks a finance cost or depreciation line |
| 3 | `tb_ledger`, `tb_vs_statements`, `stub` | some party and other ledgers; every difference line; the stub lines | Tally export variants (groups, opening rows); ledgers missing or doubled; a cash ledger mapped to the wrong account (large equal and opposite drafts) |
| 4 | `bank_closing`, `register_total`, `payroll_total`, `gst_return` | one per statement, per register sheet, per payroll file | rows dropped between PDF pages; credit notes, GST or foreign-currency columns; monthly payroll amounts (the unit answer) |
| 5 | `sales_register_vs_gl`, `purchase_register_vs_gl`, `gl_vs_tb`, `payroll_vs_gl`, `gstr1_vs_sales_register`, `gstr3b_vs_gstr1`, `gstr3b_vs_books`, `rcm` | each detail line you can work out | what the registers, books and payroll disagree on, and whether fin found exactly that |
| 6 | `bank_unmatched`, `invoice_unmatched`, `fx_line`, `customer_received`, `customer_partly_paid`, `bank_vs_tb` | per bank account and customer with receipts; every fx line | receipts matched to the wrong invoice; a false fx gain; "no booked value" where the books do hold the receipt ([client question](client-questions.md) 7) |
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
| `bank_closing` | the closing balance printed on the statement, in the account's currency, tying or not (`ledger` is the account number's token, `ACCT-0001`, as `fin eval init` lists it; `period` the statement's end date) |
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
