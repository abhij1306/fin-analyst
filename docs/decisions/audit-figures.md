# The audit of 2026-11-28: forensic and figure fixes (readings)

These are the builder's readings for the fixes to the verified audit findings on the forensic tests and on figures
that showed a missing input as zero. They are not owner decisions: the owner can change any of them.

## 1. A missing NWC component does not void the total

- **Reading:** a working capital component with no ledger confirmed to its accounts is NULL (unknown), never 0; a
  confirmed ledger with a nil balance is 0. Net working capital is the sum of the known components and
  `missing_components` names the rest; it is NULL only when every component is missing.
- **Why:** a service company has no inventories ledger. A NULL total whenever one component is missing would leave most
  companies without a working capital figure; showing the total with the missing components named says an input is
  missing without inventing a zero.
- **How to change it:** `db/schema/diligence_nwc.sql` (`nwc_year_end`, `nwc_monthly`); `report/dd_tables.py: _nwc_cells`.

## 2. A blank figure makes a reconciliation line not compared

- **Reading:** a GST return month with a blank figure, and a payroll snapshot with a blank CTC (or gross, when that is
  the measure), give a `not_reconciled` line naming the blank rows, with the unknown side NULL. The line is listed, not
  queried, and the report's GST reconciliation table counts such lines apart from the differences.
- **Why:** `not_reconciled` already means "listed, not compared" (gl_vs_tb, cut-off); no new line kind is needed.
- **How to change it:** `reconcile/gst.py: _lines`, `reconcile/payroll_vs_gl.py`, `fdd/tax_data.py: _RECON`.

## 3. A row with no balance carries the expected balance

- **Reading:** in `bank_balance_chain` a row that prints no balance is not checked; the expected balance carries
  through it, so the next row with a balance is checked against the opening plus every movement since. The rows with
  no balance are named in the test's state. A dated row with no movement that prints a balance stays in the chain.
- **Why:** restarting the chain at the next printed balance hid a break across the blank row.
- **How to change it:** `forensic/families/bank_rows.py: _chain`.

## 4. One rounding for the service-day split

- **Reading:** a service-period amount's share through a day is amount x days / total days, rounded half up to the
  paisa once (`day_share` in SQL, `ROUND_HALF_UP` in `recast.spread`). The backlog's billed-not-earned remaining is the
  amount less the share through the period end, and its next-year part the share through the next year end less that.
- **Why:** the cut-off, the backlog and the recast now show parts of one invoice that add up to it.
- **How to change it:** `db/schema/company_views.sql: day_share`.
