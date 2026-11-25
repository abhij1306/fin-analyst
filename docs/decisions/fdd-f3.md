# FDD F3: provisions, payroll liabilities and cut-off

Builder's readings of [fdd.md](../fdd.md) F3, not owner decisions. Scope readings are in
[fdd-scope.md](fdd-scope.md).

## Provision ledgers are designated, not mapped to new accounts

- **Decision**: the ledgers holding the provision for doubtful debts and the gratuity provision are named in
  `provision_ledgers`: Claude proposes them with a reason, the user confirms or rejects them
  (`fin provisions propose|confirm|reject`). No dictionary account was added. A provision figure is NULL (shown as
  `-`) until a ledger of its kind is confirmed.
- **Why**: the plan says "ledgers mapped to it", but Schedule III has no such account: the doubtful debts allowance sits
  inside trade receivables and gratuity inside provisions, and a ledger maps to one account for the statements. A new
  account would change the trial balance roll-ups other checks rely on. Naming the ledgers keeps the mapping as it is
  and still leaves the choice to the user.
- **How to change it**: `db/schema/fdd_bsheet.sql`: `provision_balances`.

## What is open at a year end

- **Decision**: receivables at a year end are the sales invoices of the latest `bank_vs_invoices` run of each period
  (D5) that are dated by the year end and were not paid by it. Per match group, the receipts dated by the year end are
  set against its invoices in the group's currency; the rest (none when they cover it) is open, shared over the
  group's invoices by amount and shown in their Amount in Rs, each aged from its own invoice date. So an unmatched
  invoice is open in full, one paid after the year end too, and an instalment or partly paid one for the part not
  received by then. An invoice in several runs counts from the latest. Only year ends whose period has a
  run are aged; invoices in no run are shown as "not aged" (`not_in_bank_run_inr`). Amounts are before GST (the
  register's Amount in Rs), while the books' receivables include GST. NEEDS CLIENT CONFIRMATION (whether the
  before-GST ageing is the comparison the client wants).
- **Why**: "from the sales register and matched receipts, D5". A period with no bank statement cannot say what was
  paid, so ageing it would show every invoice as open.
- **How to change it**: `db/schema/fdd_bsheet.sql`: `receivable_open_lines`.

## Coverage per bucket

- **Decision**: buckets are 0-90, 91-180, 181-365 and over 365 days from invoice date to the year end. Coverage of a
  bucket is the provision over the open amount of that bucket and every older one; for the total, over all of it.
- **Why**: a provision is not allocated to buckets; "coverage per bucket" read as how far the provision covers the
  receivables at least that old, which is the usual FDD reading of an ageing against a provision.
- **How to change it**: `db/schema/fdd_bsheet.sql`: `receivable_provision`.

## Indicative gratuity

- **Decision**: per employee on the snapshot not resigned by its date: completed years from joining to the snapshot
  date (whole years, no rounding up of a part year over six months); eligible at `[fdd] gratuity_min_years` (5) or
  more; 15/26 × monthly wages × years, capped at `[fdd] gratuity_cap` (Rs 20,00,000). Monthly wages = annual gross
  salary / 12 × `[fdd] basic_share` (0.5). The wage-code figure uses wages = the larger of gross × basic_share and
  half the CTC (gross salary when CTC is blank). Employees with no joining date or no gross salary are counted as
  incomplete and add nothing. Only payroll files whose unit the user confirmed annual are used. The figure is always
  labelled "indicative, undiscounted, not an actuarial valuation" with the assumption. NEEDS CLIENT CONFIRMATION
  (basic share, and whether the six-month rounding of the Act should apply).
- **Why**: the plan states the formula and the assumptions; the payroll has no basic pay column. The Payment of
  Gratuity Act counts a part year over six months as a full year; the plan says completed years, which is the more
  conservative reading for an indicative figure.
- **How to change it**: `db/schema/fdd_bsheet.sql`: `gratuity_lines`; `config.toml [fdd]`.

## [fdd] is optional in config.toml

- **Decision**: `[fdd]` is written by `fin init`; a config.toml without it, or without one of its keys, uses the plan's
  values (`FDD_DEFAULTS`). An unknown key or a value out of range is E002.
- **Why**: existing data folders have a config.toml written before FDD; the values are the plan's stated assumptions,
  shown with every indicative figure, so they are not silent.
- **How to change it**: `src/fin/config.py`: `FDD_DEFAULTS`, `_fdd`.

## Cut-off lines and what becomes a query

- **Decision**: `sales_cutoff` flags sales register lines invoiced within `[fdd] cutoff_days` (15) on either side of
  the year end whose service start is on the other side; `purchase_cutoff` flags purchase lines invoiced up to
  `[fdd] unrecorded_days` (60) after the year end whose service started on or before it. The year end is each annual
  period's (not the stub's). The two windows are copied onto the engagement when it opens, as the bank matching
  settings are, so a later config.toml change applies to the next engagement. One line per invoice of one party (its flagged lines added up; keyed by the number, with the party token
  when two parties use the same number), kind left_only with the
  amount; at or above the engagement threshold it needs a query, like any detail line. Only the service start date is
  read (service end is not). Lines in the window with no service start, and registers ending before the window ends,
  are listed as not reconciled. Both are in `RECONCILIATIONS`, so gate G2 requires them when the registers have
  lines in the window.
- **Why**: the plan calls them reconciliations with lines for queries; fin does not compare them with the books, so
  whether a flagged invoice is a real cut-off error (accrued, deferred) is the company's answer.
- **How to change it**: `src/fin/reconcile/cutoff.py`: `_flag`, `_settings`; `src/fin/diligence/engagement.py`:
  `open_engagement`.

## The indicative gratuity is not a golden kind

- **Decision**: golden kinds are `receivable_ageing` (per bucket), `doubtful_debt_provision`, `gratuity_provision`,
  `sales_cutoff` and `purchase_cutoff`. The indicative gratuity is not one.
- **Why**: it is computed on a config.toml assumption, not printed in any document; its inputs (`payroll_total`, the
  provision balance) are checked, and the formula is unit-tested.
- **How to change it**: `src/fin/evals/provisions.py`.
