# F6: GST and income tax (builder's readings)

Plan: [fdd.md](../fdd.md) F6. These are the builder's readings, not owner decisions.

## A GST return summary is one file per return and year, one row per month

- **Decision**: `fin ingest <co> <file> --kind gst --profile gstr1|gstr3b` reads a workbook with a period line
  ("1-Apr-2024 to 31-Mar-2025": a year of the engagement or exactly its stub, E808) and one row per return month
  (`Apr-2024` or an Excel date). It is stored as its return (`source_files.kind` `gstr1` or `gstr3b`), so a later
  GSTR-1 for the year supersedes the earlier GSTR-1 only. `--profile` is required with `--kind gst`: a GSTR-3B
  layout also has every GSTR-1 column, so matching by header alone could read a GSTR-3B as a GSTR-1. A month that
  cannot be read, is outside the period or appears twice is E950 and nothing loads; months of the period with no row
  are listed at load, not refused. GSTR-3B fields: taxable value and tax are outward taxable supplies (table 3.1(a)),
  ITC the net ITC claimed (table 4), RCM liability table 3.1(d), tax paid in cash and through credit (table 6.1).
- **Why**: the plan says "monthly return summaries" with a profile per return. The portal's own downloads differ by
  year and are not one layout, so fin reads an export the company or its consultant prepares, like the Tally exports.
  [Client question 8](../client-questions.md) (whether the export is fixed). A new layout gets its own profile (agent reading, 2026-11-26).
- **How to change it**: `templates/profiles/gstr1.toml`, `gstr3b.toml`; `fin/fdd/gst.py: rows`.

## Tax ledgers: one class per ledger, proposed by Claude, confirmed by the user

- **Decision**: classes `gst_output`, `gst_input`, `rcm`, `tds`, `income_tax` and `mat_credit` (added so "MAT credit
  where mapped" has something to map). A ledger has at most one class. Proposals go to `fin map tax`, decisions to
  `fin review ... --tax` (as for customer sectors); only confirmed classes are read.
- **Why**: the plan's list, plus MAT credit, which the dictionary has no account for.
- **How to change it**: `db/schema/fdd_gst.sql: tax_ledgers` CHECK and `fin/fdd/tax_ledgers.py: CLASSES`.

## The books side of GST is the gross tax booked in the month

- **Decision**: `gstr3b_vs_books` compares GSTR-3B output tax with the **credits** on the confirmed `gst_output`
  ledgers in the month (Ledger Vouchers entry date), and GSTR-3B ITC with the **debits** on the `gst_input` ledgers;
  `rcm` compares the GSTR-3B RCM liability with the credits on the `rcm` ledgers. Debits on output ledgers (the
  monthly set-off against ITC, tax payments, credit notes) and credits on input ledgers (the set-off) are not counted.
  ITC on reverse-charge supplies, booked as a debit on an input ledger, counts, as GSTR-3B's ITC includes it.
- **Why**: the net movement of an output ledger is near zero after the monthly set-off and payment, so it says nothing
  about the tax booked. Counting credits only leaves credit notes out: a month with a credit note shows the books
  higher than GSTR-3B (which is net of credit notes), and that difference is queried, not hidden.
  [Client question 8](../client-questions.md).
- **How to change it**: `fin/reconcile/gst.py: _books`.

## Lines, keys and thresholds

- **Decision**: every GST reconciliation line is one month and one measure (level `gst`, key `2024-05 taxable_value`,
  `output_tax`, `itc` or `rcm_liability`), left the return and right the other side. A line at or above the
  engagement threshold is for a query (`fin query raise --items`), like a D4 invoice line; below it is listed. A
  month only on one side is `left_only` or `right_only`; a return month of zero with nothing in the books matches.
  `gstr1_vs_sales_register` compares GSTR-1 taxable value with the sales register's Amount in Rs (before GST) by
  invoice month; `gstr3b_vs_gstr1` compares taxable value and output tax (IGST + CGST + SGST + cess).
- **Why**: the plan names the comparisons "by month"; a month is the unit a return is filed in, so it is the line a
  query names.
- **How to change it**: `fin/reconcile/gst.py`; `fin/reconcile/base.py: DETAIL_LEVELS`.

## When the GST reconciliations are required

- **Decision**: gate G2 requires each GST reconciliation for a period once its inputs are loaded: GSTR-1 and a sales
  register line in the year; GSTR-1 and GSTR-3B; GSTR-3B and Ledger Vouchers (then the output and input ledgers must
  be classed, else G2 shows it cannot run); for `rcm`, also an RCM liability in GSTR-3B or a confirmed `rcm` ledger.
- **Why**: the same rule as the D4 reconciliations (required when the inputs are loaded); a company with no reverse
  charge should not be blocked by `rcm`.
- **How to change it**: `fin/reconcile/gst.py: applies_*`.

## Tax charge

- **Decision**: `tax_charge` per annual period and scope from the statements: PBT, current tax (`tax_expense`,
  including earlier years and MAT), deferred tax, total tax (as printed, else current plus deferred with a line not
  printed counting as nil), effective tax rate (total over PBT, NULL for nil PBT), net deferred tax asset (assets less
  liabilities) and its movement from the year before, and MAT credit (the confirmed `mat_credit` ledgers' trial
  balance balance, standalone only). The deferred tax movement is shown beside the deferred tax charge, not reconciled
  to it (the difference is OCI or acquisitions, for the user to read).
- **Why**: most statements print current and deferred tax but some print only current tax; a missing line read as
  nil follows the gross margin rule in `company_views.sql`.
- **How to change it**: `db/schema/fdd_gst.sql: tax_charge`.

## Not built

- Notices and assessments come in through F5's `tax_matter` rows (plan). No GSTR-2A/2B matching, no e-invoice or
  e-way bill data, no TDS return (26Q/24Q) reconciliation: no such data is in the plan.
