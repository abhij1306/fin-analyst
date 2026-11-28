# SPEC-14: Rebuilt statements

## Summary

The client's request: the financial statements built from the books, beside the ones the company reported. fin
already rolls a trial balance up through the confirmed ledger mappings for a current-year stub (`stub_figures`).
This spec does it for every year with a trial balance and adds a cash flow derived from the balances, which gives a
second, independent figure where the reported one is in doubt.

## Scope

1. **View `rebuilt_statements`**, in the shape of `standard_statements`.
   - **P&L and balance sheet:** trial balance closing balances summed through the confirmed ledger mappings
     (`ledger_accounts`), per year and scope.
   - **Cash flow, indirect:** from the year-on-year change in the rebuilt balance sheet and the rebuilt P&L. The
     first year with no earlier trial balance has none.
   - A line that the balances alone cannot give (for example tax paid against tax charged) is "not derivable" with
     the reason. It is never estimated (Q11).
2. **Missing is not zero** ([engineering.md](../engineering.md) rule 1). A ledger with no confirmed mapping is not
   rolled up; the view carries the unmapped amount per year, and a rebuilt total with unmapped ledgers under it is
   marked incomplete.
3. **What it needs is checked first:** a ledger-level trial balance (not group totals) for each year. A year
   without one, or with group totals only, is listed with what to ask the company for.
4. **`fin statements <co> --rebuilt`** and a Statements screen option: reported, rebuilt and difference side by
   side for each line, in the display unit and databook layout (SPEC-06). Each rebuilt figure traces to its
   ledgers, and each ledger to file and sheet row.
5. **Nothing reported changes.** A difference is a row for SPEC-15, to be queried or adjusted with evidence.

Out of scope: consolidation; rebuilding from vouchers instead of the trial balance; correcting the books.

## Acceptance criteria

- [ ] On the synthetic diligence company, rebuilt P&L and balance sheet equal reported where no difference is
      planted, and differ by exactly the planted amounts elsewhere.
- [ ] Rebuilt cash flow: opening cash plus the three activity totals equals closing cash in every year (SQL test).
- [ ] Removing one ledger mapping marks the totals above it incomplete and shows the unmapped amount.
- [ ] A group-only trial balance fixture is refused for rebuilding with a message that says what to ask for.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Rebuild the P&L and balance sheet from the trial balance for every year | `db/schema` views, `src/fin/reconcile`, docs | criteria 1, 3, 4 |
| 2 | Derive an indirect cash flow from the rebuilt balances | views, `docs/decisions/` | criterion 2 |
| 3 | Show reported, rebuilt and difference in statements and on the Statements screen | `statements.py`, UI, export | UI test |

Risk: calculations and a schema file. A separate reviewer; Opus medium builder for PR 2.
