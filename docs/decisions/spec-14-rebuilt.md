# SPEC-14 rebuilt statements (PR 1 to 3): builder's readings

Readings, not owner decisions, until the owner confirms them. Owner decision Q11 (no estimates in a cash flow
rebuilt from balances) is applied as given.

## The cash flow starts from profit for the year

- **Decision**: operating activities start from profit for the year (`cf_pat`), add back depreciation, and take
  the change in each working capital account. Income taxes paid and finance costs paid are "not derivable" lines
  with the reason; the tax charge and the change in tax balances stay in operating activities, the finance cost
  charge in profit for the year.
- **Why**: starting from profit before tax needs a tax paid line, which Q11 says is never estimated. Starting after
  tax keeps every rupee of the balance changes in one of the three activities, so opening cash plus the activities
  equals closing cash.
- **How to change it**: `src/fin/rebuilt/sql.py` (`cf_items`) and `NOT_DERIVABLE` in `src/fin/rebuilt/view.py`.

## Every gross flow the balances net is "not derivable"

- **Decision** (reviewer): besides tax and finance costs paid, interest and dividends received, sale proceeds of fixed
  assets and of investments, dividends paid and the exchange rate effect on cash are "not derivable" lines; each
  reason says which line holds the amount instead. The activity totals stay as derived: every rupee is in one of
  them, so they tie to cash, but where an amount sits can differ from the company's classification (interest paid
  is in operating activities, not financing).
- **Why**: Q11 (never estimate), and a reader must see that these lines exist but cannot be split out.

## Purchases of fixed assets are the change in the net block plus depreciation

- **Decision**: `cf_capex` is minus the change in the fixed asset accounts (net block, work in progress,
  intangibles, right-of-use assets, goodwill) minus the year's depreciation: purchases net of disposals.
- **Why**: a trial balance holds closing balances only, so purchases and disposals cannot be told apart; the net
  figure is what the balances give.
- **How to change it**: `_LINES` in `src/fin/rebuilt/view.py`.

## Each balance sheet account has one cash flow line

- **Decision**: `CASH_LINES` (`src/fin/rebuilt/view.py`) names the line for each Schedule III balance sheet
  account; an account it does not name goes to other current assets (debit side) or other current liabilities
  (credit side). The change in other equity less the year's profit is "Other changes in equity (dividends and
  other movements)", a financing line with no dictionary account.
- **Why**: every balance must land somewhere for the cash flow to tie; dividends and other equity movements cannot
  be told apart from the balances.
- **How to change it**: `_LINES` in `src/fin/rebuilt/view.py`.

## An unmapped ledger marks the totals it could be under

- **Decision**: a ledger waiting for review marks its proposed account's subtotals incomplete; one with no proposal
  (pending or rejected) marks every total of its year, since fin cannot say where it belongs. Every cash flow line
  of a year is incomplete when that year or the year before has an unmapped ledger.
- **Why**: missing is not zero (engineering.md rule 1); naming the totals is the most fin can say without guessing.
- **How to change it**: `placed`, `loose` and `bs_pl` in `src/fin/rebuilt/sql.py`.

## The rebuild is standalone, over the latest engagement

- **Decision**: `rebuilt_ledgers` reads the loaded trial balances of the latest engagement; the rebuilt rows are
  scope standalone, compared with the standalone reported statements. `--scope consolidated` is refused.
- **Why**: a trial balance is one company's books; consolidation is out of scope in the spec.

## A year's cash flow needs the trial balance ending the day before it starts

- **Decision**: a period has a cash flow when a loaded trial balance ends on the day before it starts. The stub (a
  year to date) is rebuilt like a year and gets a cash flow from the last annual year's trial balance.
- **Why**: the change in balances is only a year's movement when the two balances are next to each other.

## The difference is blank when either side has no figure

- **Decision**: difference = rebuilt - reported, computed in SQL, and NULL when reported or rebuilt is missing (a
  subtotal the company did not print, a line no confirmed ledger reaches).
- **Why**: missing is not zero; a blank says there is nothing to compare.

## The code lives in `fin/rebuilt/`, not `fin/reconcile/`

- **Decision**: the spec names `src/fin/reconcile`; the view and comparison are in a new package `src/fin/rebuilt/`.
- **Why**: `fin.reconcile` imports every D4 reconciliation when it loads, and `fin sql` (which makes the new view)
  is imported by the diligence modules those import, which is a cycle. The rebuild records no run either.

## A Statements screen before SPEC-06

- **Decision**: the web UI gets a Statements tab now: the latest fiscal year as reported, or reported vs rebuilt
  (`?view=rebuilt`), in the current statements layout and document unit. SPEC-06 restyles it with the databook
  layout and display settings.
- **Why**: the spec's row 3 shows the rebuilt view on the Statements screen, and SPEC-06, which adds that screen, is
  not built yet.

## A group-only trial balance is checked again

- **Decision**: `fin ingest` already refuses a trial balance with group rows only (E809); the rebuild still checks
  each year for ledger rows and says what to ask for (E822 when the year is asked for or none can be rebuilt).
- **Why**: the rebuild must not depend on how a file was loaded.
