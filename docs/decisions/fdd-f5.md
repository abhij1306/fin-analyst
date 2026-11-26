# FDD F5: contingencies, commitments, notices and related parties

Builder's readings of [fdd.md](../fdd.md) F5. None is an owner decision.

## One reading CSV for every kind of item

- **Decision**: `fin disclosures load` takes one CSV with a `kind` column (nine kinds, the plan's list) rather than one
  file per schedule. Required per row: kind, period, nature, page; a related party row also needs counterparty and
  relationship; a related party transaction or balance needs counterparty and amount. The period must be a period of
  the open engagement (or its stub). Loading the same document (same sha256) again replaces its rows; another document
  adds rows.
- **Why**: the notes to accounts hold all the kinds in one document; one reading per document keeps file + page with
  each row, as `fin recast contracts` does.
- **How to change it**: `fin/disclosures/reading.py`: `KINDS`, `_check_kind`.

## Amounts carry the document's unit

- **Decision**: the reading has an optional `unit` column (rupees by default; thousand, lakh, crore, million); fin
  turns amount, demand and paid under protest into rupees with the same conversion as statements.
- **Why**: notes are usually printed in lakhs or crores; converting in fin keeps Claude from doing arithmetic.
- **How to change it**: `fin/disclosures/reading.py`: `_money`.

## Related parties are matched by token

- **Superseded 2026-11-26** by [masking.md](masking.md): a counterparty that is a register customer or vendor gets its token, one that is a loaded ledger's name becomes that ledger, any other is stored as read (no `PRTY-`); a ledger meets a token through `mask_values`.

- **Decision**: a counterparty is masked on load with the existing masking: a name fin already masked (a customer,
  vendor or party ledger, ignoring case and spaces) gets that token; a new name gets a `PRTY-` token. A related party
  is "matched" to a customer, vendor or ledger when it is the same token (view `related_party_matches`), with no
  review. Everything else is a link Claude proposes by token and only counts once the user confirms it. fin does not
  match by partial name (`ABC Pvt Ltd` vs `ABC Private Limited`): Claude proposes that link. NEEDS CLIENT
  CONFIRMATION (whether exact-name matches should also wait for the user).
- **Why**: the plan says fin matches by normalised name against `mask_values` and never prints names; the token is
  exactly that match, and the same name already means the same party everywhere else in fin.
- **How to change it**: `db/schema/fdd_disclosures.sql`: `related_party_matches`.

## What rpt_vs_books compares

- **Decision**: a related party transaction row is compared only when the reading says with what (`books`): `sales`
  (the sales register, Amount in Rs before GST, of the customers linked to the party, by the line's period),
  `purchases` (the purchase register of its vendors), `debit` or `credit` (the period's Ledger Vouchers debits or
  credits on the named `ledger`). Rows with the same party, books and ledger are added up. difference = disclosed −
  books. A line whose books side cannot be read (no register lines or Ledger Vouchers for the period, or no customer
  or vendor linked to the party yet) is `not_reconciled`: listed with the reason, never queried. A line at or above
  the engagement threshold is for Claude to put in a query; below it is listed. rpt_vs_books is in
  `RECONCILIATIONS`, so gate G2 requires it (ran on current data, every line at or above the threshold in a query)
  once a loaded reading has a compared transaction.
- **Why**: the disclosed natures (remuneration, rent, loans taken) are free text and do not map to one books figure;
  Claude reads which figure a disclosure corresponds to, and naming the ledger avoids mixing a party's sales and loan
  postings. An unlinked party is a missing mapping, not a company difference, so it is not queried.
- **How to change it**: `fin/disclosures/rpt.py`: `rpt_vs_books`; `fin/reconcile/__init__.py`: `RECONCILIATIONS`.

## related_party_transactions, pricing and loans

- **Decision**: `related_party_transactions` shows per party and period the linked customers' sales, vendors'
  purchases (register Amount in Rs) and linked ledgers' Ledger Vouchers entries per voucher type (debit, credit,
  debit − credit). `related_party_pricing`: per period and product, the party's Amount in Rs / quantity against the
  same for all customers (or vendors) not linked to any related party, over register lines with a product and a
  quantity above 0; difference % = (party − others) / others × 100. `related_party_loans`: linked ledgers confirmed to
  a loan or borrowing account (loans_non_current, loans_current, loans, lt_borrowings, st_borrowings, borrowings); the
  balance at each year end from the trial balance (debit − credit), the average of the previous and this year end (the
  closing alone when no previous trial balance is loaded, labelled), interest = the entries on the loan ledger whose
  other ledger is confirmed to finance_cost, interest_expended, other_income or interest_income, netted
  (|debit − credit|, so a reversed accrual cancels), and NULL when the period has no loaded Ledger Vouchers (not
  known, never shown as zero); the implied rate = interest / |average| × 100.
- **Why**: the plan names the three views; these are the simplest figures the loaded files support. The implied rate
  on a two-point average is approximate, and the view says which average it used.
- **How to change it**: `db/schema/fdd_disclosures.sql`.

## Not built

- **Decision**: no check of related party balances against the trial balance, no comparison of disclosed contingent
  liabilities or commitments with the books, no assessment of litigation or tax matters (fin records forum, stage,
  demand and amount paid; the assessment is the user's).
- **Why**: the plan's reconciliation is for transactions; the rest is judgement (docs/decisions/fdd-scope.md).
- **How to change it**: add a reconciliation in `fin/disclosures/`.
