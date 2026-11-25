# d7-qoe: working capital, the diligence report and its dashboard

Product-level decisions of the D7 workstream (DILIGENCE_PLAN.md §7 D7, §4 `nwc_year_end`, `nwc_monthly`). Each is a builder's reading until the owner confirms it (see README.md in this folder).

## 1. Which ledgers are working capital

- **Decision:** Ledgers the user confirmed to trade receivables, inventories, other current assets, current loans, other current financial assets (assets) and trade payables, other current liabilities, other current financial liabilities, short-term provisions (liabilities). Net working capital = assets - liabilities. Cash, bank balances, current investments, borrowings, lease liabilities and current tax assets and liabilities are not in it.
- **Why:** The plan says "working capital accounts" without a list. Trade working capital excludes cash, debt and tax; they are net debt or tax items. Current loans and advances in a Tally trial balance are mostly advances to suppliers and staff.
- **How to change it:** `db/schema/diligence_nwc.sql`: the `nwc_accounts` view.

## 2. Year-end working capital is the trial balance as filed, the stub included

- **Decision:** Each loaded trial balance (audited years and the stub) is rolled up through confirmed ledger mappings, balances in the statements' sign. A ledger not confirmed to an account is counted in `unmapped_ledgers` and left out of every component. The stub is labelled unaudited management accounts and is never annualised.
- **Why:** Plan §4; the stub is year to date, so its balance sheet is a point in time and needs no annualising.
- **How to change it:** `diligence_nwc.sql`: `nwc_year_end_lines`, `nwc_year_end`.

## 3. The monthly view uses only ledgers that tie, and says what it left out

- **Decision:** A working capital ledger is in the monthly view when the previous period's trial balance is loaded (the previous annual period; the last annual period for the stub) and the latest `gl_vs_tb` run of the period has its movement equal to the trial balance. The `gl_vs_tb` run must be on the loaded Ledger Vouchers and trial balance and later than the previous trial balance's load (else "gl_vs_tb has not run on the current files"). A ledger absent from the previous trial balance opens at nil, as in `gl_vs_tb`. Other ledgers are left out with the reason, and every month shows how many ledgers are in and out, so a month with ledgers left out is not read as the complete working capital. A period with no previous trial balance has no monthly view.
- **Why:** Plan §4 (an inferred opening would make the check circular); showing the partial coverage keeps a part view from looking complete.
- **How to change it:** `diligence_nwc.sql`: `nwc_monthly_ledgers` (the `status` CASE), `nwc_monthly`.

## 4. Month ends, and the stub's last month

- **Decision:** One row per calendar month of the Ledger Vouchers file's period, at the month's last day; the stub's last month ends on the stub's end date. A month with no voucher carries the balance forward.
- **Why:** There are no monthly trial balances; month-end balances are opening plus movements to that date.
- **How to change it:** `diligence_nwc.sql`: the `months` CTE of `nwc_monthly_lines`.

## 5. The report describes and never judges, with typed numbers refused

- **Decision:** The narrative uses `{{figure:<name>:<period>}}` references only; any other digit stops the render (E602). The report never states a recommendation, rating or verdict. Proposed adjustments are shown marked "not applied" and are not in adjusted EBITDA. Other income is shown below EBITDA. Pass-through revenue is shown as project revenue beside core revenue. The stub is labelled unaudited management accounts wherever it appears. Names are tokens.
- **Why:** The owner's report rules (CLAUDE.md); plan §7 D7.
- **How to change it:** `report/dd_data.py: FIGURES` (which numbers can be referenced), `report/dd_tables.py`, `templates/report/diligence.qmd`.

## 6. The gate states are shown, not required

- **Decision:** The report renders whatever the gate states are, and prints each gate's state when it was rendered (an open engagement: as evaluated now; a closed one: approved if it was ever approved). It does not refuse on an open gate.
- **Why:** Plan §5 has no report gate ("the planned G5 is dropped"); the report shows the states so the reader knows what it rests on.
- **How to change it:** `report/dd_data.py: gate_states`; to refuse, call `gates.require` in `dd_render.make_diligence_report`.

## 7. Collections and customers sections are the existing outputs

- **Decision:** The collections table is `customer_collections` (D5) summed per period, plus one row per customer; the customers, sectors and pricing section is the health report's (D9) as it is; the recast section is the D6 reconciliation (period, month, undated invoices) and the contracts read, shown beside it as evidence only. Nothing new is computed for them.
- **Why:** The brief asks for them in the report; new analytics are out of scope.
- **How to change it:** `report/dd_data.py` (`_COLLECTIONS`, `_RECAST`), `report/dd_tables.py`.
