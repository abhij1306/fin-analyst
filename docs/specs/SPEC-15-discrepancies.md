# SPEC-15: One list of differences

## Summary

The client's request: every difference in one place, from the headline figure down to the invoice. fin has the
reconciliations (`fin reconcile`, `recon_items`): trial balance against statements, registers against ledger,
ledger movements against trial balance, payroll against ledger, bank against invoices. Each is its own list. This
spec joins them into one chain and ranks it.

## Scope

1. **View `discrepancies`.** One row per difference, at one of four levels, each pointing to its parent:

   | Level | Compares |
   |---|---|
   | Headline | a reported line against the rebuilt one (SPEC-14), and against a reference databook (SPEC-10) |
   | Account | the ledgers that make up a headline difference |
   | Party | customers or vendors: register against ledger |
   | Transaction | invoices or vouchers: missing, duplicated, different amount, exchange difference |

   Each row carries both sources (file and cell or row), both amounts, the difference, whether it is over the
   engagement threshold, and the query raised for it, if any. The ladder runs registers, ledger, trial balance,
   statements, reference databook.
2. **A child explains its parent, and the rest is shown.** Under each difference: the amount its children explain
   and the amount still unexplained. The unexplained part is never spread or hidden.
3. **`fin discrepancies <co> [--level] [--over-threshold] [--json]`**, ranked by amount, in the display unit.
   It is a sheet of the Excel export and a screen with drill-down from headline to transaction.
4. **To a query in one step.** From the screen or the CLI, selected rows go to `fin query raise --items` as today:
   the assistant drafts, the user approves. A row shows its query's status.
5. **Nothing here changes a figure.** Differences are queried or become adjustments with evidence.

Out of scope: new reconciliations; matching rules; automatic query raising.

## Acceptance criteria

- [ ] On the synthetic company with planted differences at each level, the view lists exactly those, each child
      under the right parent.
- [ ] For every parent, explained plus unexplained equals its difference (SQL test).
- [ ] A row with a raised query shows the query id and status; closing the query updates the row.
- [ ] Names in party and transaction rows follow the masking registry ([engineering.md](../engineering.md) rule 5).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Join the reconciliations into one discrepancies view with levels and parents | `db/schema` views, `src/fin/reconcile`, docs | criteria 1, 2, 4 |
| 2 | Add fin discrepancies and the export sheet | CLI, `diligence/export_xlsx.py` | CLI test |
| 3 | Add the differences screen with drill-down and query hand-off | `src/fin/ui` | criterion 3 |

Risk: masking and a schema file. A separate reviewer.
