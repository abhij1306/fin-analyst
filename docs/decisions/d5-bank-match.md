# D5 bank ↔ receipts ↔ invoices: product decisions

Only decisions that affect the analysis or what the user sees. Plan: docs/DILIGENCE_PLAN.md §7 D5.

## 1. Receipts are bank credits; invoices are sales register lines
- **Decision:** a receipt is a credit line of a loaded bank statement; an invoice is a sales register line. A statement
  that does not tie is used only once the user has accepted its `bank_tie` finding (as decided in d3-bank.md).
- **Why:** these are the two documents the plan names; Ledger Vouchers receipts are used only for the rupee value of a
  foreign-currency receipt (decision 5).
- **Change:** `reconcile/bank_lines.py: load`.

## 2. The three passes and what each compares
- **Decision:** (1) `reference`: the invoice number appears as a whole token in the receipt's reference or remarks;
  the amounts need not agree (a difference is a line); several receipts or invoices naming each other are one group; an
  invoice number that appears twice is never matched this way. (2) `party_amount_date`: same customer token, same
  amount, dates at most `match_window_days` apart; closest dates first, then the earlier invoice. (3) `amount_date`:
  same amount and window, only when neither the receipt nor the invoice has another candidate, and not when both
  customers are known and differ. A receipt's customer is the CUST token in its remarks. Anything else is left for Claude.
- **Why:** plan wording ("no fuzzy or ML matching"); pass 3 has no party to disambiguate so it never guesses.
- **Change:** `reconcile/bank_match.py: match, _reference_groups, _pairs`.

## 3. Amounts are compared in the receipt's currency, as filed
- **Decision:** an INR receipt is compared with the invoice's Amount in Rs (before GST, as the register gives it); a USD
  receipt with the invoice's own-currency Amount. Different currencies never match by pass. A receipt that includes GST
  or has tax deducted at source therefore matches only through its invoice number (pass 1, as a difference line) or
  through a proposal.
- **Why:** the register has no GST columns (§9 item 5); no tax is assumed.
- **Change:** `reconcile/bank_lines.py: load` (invoice amount), `reconcile/bank_match.py: _pairs`.

## 4. Date window, due days and settings
- **Decision:** the window is an absolute difference of dates (a receipt may precede its invoice), default 60 days
  (`[diligence] match_window_days`). `fx_window_days` (7) bounds the Ledger Vouchers receipt for a rupee value;
  `invoice_due_days` (30) is when an unmatched invoice counts as overdue.
- **Why:** the plan says "date window (config)" without a number; the register has no due date.
- **Change:** `config.py` defaults and `config.toml [diligence]`; an engagement copies them when it opens (like the threshold), so a change applies to the next engagement.

## 5. FX lines
- **Decision:** a matched foreign-currency group whose currency amounts agree is compared in rupees: the receipt's booked
  rupees (the credit of a Receipt voucher on the customer's Ledger Vouchers ledger, nearest date within
  `fx_window_days`, each used once, at a rate within 10% of the invoice's: `docs/decisions/test-run.md`) against the invoice's Amount in Rs. A difference is an `fx` line, difference =
  receipt − invoice (gain positive, loss negative); it is listed and never queried or plugged. No Receipt voucher found:
  the match carries the note `no_booked_value` and no fx line is made. A group whose currency amounts differ is a
  `difference` line in that currency, with no fx line.
- **Why:** plan §7 D5 and §9 item 5; the rupee value of a foreign receipt exists only in the books.
- **Change:** `reconcile/bank_lines.py: _group_item, _Booking`.

## 6. What is queried
- **Decision:** a `difference`, `left_only` or `right_only` line at or above the threshold in rupees is marked needs a query, for Claude to group (`fin query raise --items`, as for D4)
  (a foreign-currency line is converted at its invoice's rate, or the booked value for a receipt; with no rupee value
  it is queried). An unmatched invoice that is not yet past `invoice_due_days` at the period end is listed
  (note `not yet due`), not queried: an unpaid invoice that is not yet due is not a difference.
- **Why:** plan: "every line at or above the threshold is in a query (`fx` lines are listed, not queried)"; the due
  exception keeps normal receivables out of the accountant's queue. Not in the plan: change it if all unmatched lines
  should be queried.
- **Change:** `reconcile/bank_lines.py: _wants_query`.

## 7. Periods
- **Decision:** matching runs over all the engagement's receipts and invoices once; the results are split by period: a
  receipt in the period of its statement, an invoice in the period of its date (only periods that have a bank
  statement), a matched group in its receipts' period. A period with no bank statement lists none of its invoices.
- **Why:** an invoice raised in one year is often paid in the next; matching per period would lose it.
- **Change:** `reconcile/bank_lines.py: build`.

## 8. Collection days and ageing
- **Decision:** collection days = average days from invoice to receipt over a customer's matched invoices, weighted by
  Amount in Rs (a group of several lines uses its latest dates). Open invoices are aged at the period's last day: not
  due (up to the due days), then overdue 1–30, 31–60, 61–90 and over 90 days past the due days.
- **Partial payments:** an invoice matched with a difference (a short payment, tax deducted) counts as received in full
  in the view; the shortfall is its own `difference` line, queried like any other, not an open invoice.
- **Change:** `db/schema/sources.sql` view `customer_collections`.

## 9. Claude's proposals
- **Decision:** a proposal joins bank credit lines and invoice lines (n:m); it is applied only after the user confirms it
  (`fin review confirm <co> <ids> --matches`), first of all passes, as pass `confirmed`, the next time the
  reconciliation runs; if one of its lines was matched by then it is skipped. A confirmed match that does not agree in
  amount is a difference line like any other.
- **Change:** `bank_proposals.py`, `reconcile/bank_match.py: match`.

## 10. The stub's bank cross-check (`bank_vs_tb`)
- **Decision:** per period with a trial balance, the INR statements ending on the period's last day (the latest per
  account) are summed and compared with the trial balance ledgers confirmed to `cash`, `other_bank_balances` or
  `cash_and_bank_balances`; one line, difference = bank − books, needs a query at or above the threshold unless a ledger is
  unmapped. Foreign accounts are not compared (no rate) and are counted in the line's note. A statement not ending on
  the last day is not compared.
- **Why:** plan: "the stub is cross-checked … vs bank in D5"; a trial balance does not say which ledger is which bank
  account, so the cash and bank ledgers are compared in total.
- **Change:** `reconcile/bank.py: bank_vs_tb`.

## 11. G2
- **Decision:** with a bank statement loaded, G2 also needs `bank_vs_invoices` (and, for periods with a trial balance and
  INR statements ending on the last day, `bank_vs_tb`) to have run on the data as it is now, and every line at or above
  the threshold to be in a query. Without a loaded sales register, `bank_vs_invoices` cannot run (E840) and G2 fails.
- **Change:** `reconcile/checks.py` (shared with D4).
