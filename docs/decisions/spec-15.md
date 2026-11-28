# SPEC-15 discrepancies (PR 1 to 3): builder's readings

Readings, not owner decisions, until the owner confirms them.

## The headline level is tb_vs_statements

- **Decision**: the headline rows are the stored `tb_vs_statements` lines (trial balance rolled up by confirmed
  mapping against the reported statement line), the same comparison as SPEC-14's rebuilt - reported, and each
  reference figure that differs from fin's (`reference_comparison`, SPEC-10, fin - the reference).
- **Why**: a stored line has an item id, a threshold and a query link, which the drill-down and the query hand-off
  need. A reference row has none of them: it is a root with id `ref-<value_id>`, compared with the latest engagement's
  query threshold, and goes to `fin reference query` (one query per figure). A reference query is not linked to its
  figure in the database, so the row shows no query status; the Queries screen lists it. A figure fin does not hold
  (no_fin_figure) is missing, not a difference, and is not a row.
- **How to change it**: `sources()` in `src/fin/discrepancies/view.py`.

## Five reconciliations, differing lines only

- **Decision**: the view reads the five reconciliations the spec names (`tb_vs_statements`, `gl_vs_tb`,
  `payroll_vs_gl`, the two registers, `bank_vs_invoices`), and only lines that differ (`difference`, `left_only`,
  `right_only`, `fx`). Month roll-ups, matched, unmapped and not reconciled lines are not rows.
- **Why**: a month line repeats its invoices; the other kinds are not differences.

## Parents

- **Decision**: an account or party row is under the headline row of its period for its own account, else the
  nearest account above it in the dictionary tree. A register party row's account is revenue, or the one purchase
  account the ledgers are confirmed to (none when they are confirmed to two). An invoice row is under its party row
  (a new `recon_items.party` column holds the invoice's party token). Bank lines have no parent: they are roots.
- **Why**: a register party has no single ledger, so it cannot sit under a `gl_vs_tb` ledger row.

## Explained and unexplained

- **Decision**: explained = the sum of the rows directly under a row (0 with none); unexplained = difference -
  explained. A child with no rupee value (a foreign-currency line without a rate) leaves explained blank.
- **Why**: missing is not zero, and nothing is spread or allocated.

## The export sheet is in fin export

- **Decision**: the "Differences" sheet is added to the `fin export` workbook, not to the accountant query export
  (`diligence/export_xlsx.py`), unlike the PR plan's file name.
- **Why**: the query export is what is sent to the company's accountant (approved queries only); the differences are
  the analyst's working view, beside the statements and rebuilt sheets.

## The screen hands ticked rows to the assistant

- **Decision**: ticking rows and choosing "Draft a query" opens the chat with a request to draft one query from them
  (`fin query raise --items`); the screen does not raise it.
- **Why**: the assistant drafts queries and the user approves them on the Queries screen; the UI never writes the
  question itself.
