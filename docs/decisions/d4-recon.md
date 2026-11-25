# Decisions: d4-recon

Product-level choices for the register, ledger, trial balance and payroll reconciliations (docs/diligence.md §7 D4), written 2026-11-25 by the builder. Each is a builder's reading until the owner confirms or changes it.

## 1. Only invoice, ledger and payroll lines are queried; month and party lines are roll-ups

- **Decision:** Register reconciliations list a month, a party and an invoice level. A query is raised only on invoice lines (gl_vs_tb: ledger lines; payroll_vs_gl: the one period line) at or above the threshold, as plan §3 says (threshold per transaction). Month and party lines are listed.
- **Why:** Every invoice and voucher is in the invoice level, so a month or party difference is the sum of invoice lines; querying all three levels would count one difference three times.
- **How to change it:** `reconcile/base.py:DETAIL_LEVELS`.

## 2. How a register invoice meets a ledger voucher

- **Decision:** Pass 1: the register's invoice number equals the voucher number. Pass 2: of what is left, the same party token and exactly the same rupee amount (the note says the voucher number differs). Lines with the same number are added up on each side; a purchase invoice is identified by vendor and number (vendors number their own invoices), so the same number from two vendors stays two invoices and is shown with its vendor. Anything else is left_only (register only) or right_only (books only).
- **Why:** The Ledger Vouchers export has no supplier invoice number, so purchase vouchers rarely carry the vendor's number; party plus amount is the only deterministic link. No fuzzy matching.
- **How to change it:** `reconcile/registers.py:_pairs`.

## 3. Which ledgers are the books side

- **Decision:** Sales: Ledger Vouchers entries (credit less debit) of ledgers the user confirmed to `revenue`. Purchases: (debit less credit) ledgers confirmed to `purchases_stock_in_trade` or `cost_of_materials`. Payroll: ledgers confirmed to `employee_expense`. With none confirmed the reconciliation refuses (E840) instead of showing every register line as missing.
- **Why:** Only confirmed mappings count everywhere else; GST sits on its own ledgers, so these entries are before GST like the register's Amount in Rs. Other operating income mapped to revenue shows up as right_only, which is a real question for the accountant.
- **How to change it:** `reconcile/registers.py:SALES, PURCHASE`, `reconcile/payroll_vs_gl.py:ACCOUNTS`.

## 4. fx lines

- **Decision:** A foreign-currency invoice on both sides whose rupees differ, with the books' amount within 10% of the register's, is an `fx` line: listed, never queried, never plugged. The note gives the register rate, the ledger rate and FX gain or loss (sales: books above register is a gain; purchases: books below register). A larger difference is a normal difference and is queried.
- **Why:** The plan says the rate difference is an fx line, but a wrong amount on a USD invoice must not hide as one.
- **How to change it:** `reconcile/registers.py:RATE_BAND`, `_fx`.

## 5. gl_vs_tb opening, and the profit rolled into other equity

- **Decision:** Profit and loss ledger: movement vs the trial balance closing. Balance sheet ledger: movement vs closing less the closing in the prior period's trial balance (the previous annual period; the last annual period for the stub). With no prior trial balance the ledger is `not_reconciled`, listed, not queried. Ledgers confirmed to `other_equity` are compared as one line, with the prior period's profit and loss ledgers' net added to their movement, because Tally closes the profit into Profit & Loss A/c without a voucher. A ledger not confirmed to an account is `unmapped`, not compared.
- **Why:** Plan D4; an opening inferred from the same movements would be circular. Without the roll-in every year would show the prior year's profit as a difference.
- **How to change it:** `reconcile/gl_vs_tb.py:gl_vs_tb, _retained`.

## 6. payroll_vs_gl compares CTC with the books' employee expense

- **Decision:** One line per annual period: the payroll snapshot's CTC (gross salary when no row has one) vs the debit less credit of the employee expense ledgers over the year. It refuses unless the user confirmed the unit is annual. The stub has none. An employee with no value in the measure adds nothing, and the line's note says "INCOMPLETE" with how many.
- **Why:** CTC includes employer costs, so it is closer to the books than gross. The snapshot omits earlier leavers, so a difference is expected and is a question, not an error; the note says so.
- **How to change it:** `reconcile/payroll_vs_gl.py:payroll_vs_gl`.

## 7. Queries on these lines are raised by Claude, grouped, and kept across reruns

- **Decision:** fin drafts no query for these lines. `fin query raise --items` makes one draft issue from lines of one period that need a query and are in none; its amount is the lines' differences added without signs. Running again keeps a query on a line whose level, key, kind and amounts are unchanged; a changed line needs a new query. A withdrawn query leaves its lines open (G2 counts them as not in a query, and they can be raised again). Lines can only be raised while the data is unchanged since the run.
- **Why:** Plan D4: Claude groups, the user approves.
- **How to change it:** `reconcile/items.py`, `reconcile/store.py:_earlier_query`.

## 8. G2 requires a reconciliation when its inputs are loaded

- **Decision:** For each period (annual and stub) G2 requires sales and purchase register vs GL when the period's Ledger Vouchers and registers covering it are loaded, gl_vs_tb when its trial balance and Ledger Vouchers are, payroll_vs_gl when an annual period has a payroll file and Ledger Vouchers. Each must have run on the current data with every line at or above the threshold in a query; fx, not_reconciled and unmapped lines are listed. With no registers loaded the register reconciliations are not required.
- **Why:** Plan §5 says every required reconciliation ran; G1 does not require registers, so neither does G2.
- **How to change it:** `reconcile/checks.py:required`.
