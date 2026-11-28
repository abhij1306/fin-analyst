# SPEC-09: diligence in the UI

Builder's readings (2026-11-28), for the owner to confirm or change.

## Where adjustments land in the adjusted P&L

- **Decision**: the view `adjusted_pl` applies an accepted adjustment's revenue effect to `revenue` and
  `total_income`, its cost effect to `total_expenses`, and its EBITDA effect to `profit_before_exceptional_items`
  and `pbt`. A reclassification moves `moved_amount` from `from_account` to `account`. Every other line is as
  reported, including the account an adjustment names: the effect is on the totals, not split into detail lines.
- **Why**: an adjustment carries a revenue and a cost effect, not a split by line. Putting it on the totals keeps
  "adjusted totals = reported totals + accepted adjustments" exact (a test checks it in SQL) without guessing which
  detail line the company meant.
- **How to change it**: `src/fin/db/schema/diligence_views.sql: adjusted_pl`.

## The adjusted P&L ends at profit before tax

- **Decision**: the adjusted P&L shows the lines down to `pbt`. Tax and profit after tax are not adjusted.
- **Why**: fin does not compute the tax effect of an adjustment; an adjusted PAT would be a number fin made up.
- **How to change it**: `src/fin/diligence/adjusted.py: LINES_SQL` (the position cut-off).

## Money in the document's unit

- **Decision**: the Bridge screen shows money in the documents' unit (as `fin statements` and `fin engagement
  bridge`).
- **Why**: SPEC-06's display unit is not built on this branch; when it lands the screen should use it.
- **How to change it**: `src/fin/ui/diligence_screens.py`.

## The walk-sheet import is on the Queries screen

- **Decision**: the upload sits in the Adjustments card of the Queries screen, where the imported adjustments are
  listed and accepted.
- **Why**: one place for adjustments; the spec names no screen for it.

## One error code for every import refusal

- **Decision**: a file fin cannot read, a bad column and a bad row are all E847. A row the adjustment checks refuse
  (E804, E805) shows that code inside the E847 message, after the row.
- **Why**: the user fixes the file and imports it again in every case; the row and the reason are in the message.

## Evidence is the description cell

- **Decision**: each imported adjustment's `source_ref` is the file, sheet and the row's description cell
  (`walk.xlsx Walk!G5`), then the row's source reference.
- **Why**: the spec asks for the file and cell; the description cell is the one a reader goes to.

## `fin query raise` keeps its CLI body

- **Decision**: the UI's Raise calls `fin.services.diligence.raise_query`, which calls the same domain function
  (`queries.raise_manual`) as the CLI; the CLI command was not moved onto the service.
- **Why**: the CLI's options and output stay as they are; both paths share the domain function and its checks.
