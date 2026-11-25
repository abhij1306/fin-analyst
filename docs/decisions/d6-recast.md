# d6-recast: service-period recast and gate G3

Product-level decisions of the D6 workstream (docs/diligence.md §7 D6, §4 contracts, §5 G3). Each is a builder's reading until the owner confirms it (see README.md in this folder).

## 1. "Revenue booked by period" is the sales register by invoice date

- **Decision:** The recast of a period is compared with the sales register's Amount in Rs (before GST) of invoices dated in that period. The difference (recast less booked) is the period's revenue effect. It is not compared with the statements' or the ledgers' revenue.
- **Why:** The plan says "revenue booked by period" without naming a source. The register is the line-level source the recast spreads, so the two sides share a basis; the register is already reconciled to the ledgers by `sales_register_vs_gl` (D4), and any difference there is queried on its own.
- **How to change it:** `reconcile/recast.py:compute` (the `booked` side); to compare with ledger revenue, read the ledger entries as `reconcile/registers.py` does.

## 2. Straight-line is by service day

- **Decision:** A line's Amount in Rs is spread over its service period (start and end date both included) in proportion to the days in each month, rounded to paise; the last month takes the remainder so the shares add up exactly. A 12-month service from 1 May recognises 31/365 of the amount in May.
- **Why:** "Straight-line by month" is ambiguous: equal monthly instalments, or by days. Days gives the same share for the same service whatever the month lengths and is exact at a year-end that falls inside a month.
- **How to change it:** `reconcile/recast.py:spread` (for equal instalments, divide by the number of months touched).

## 3. A line without usable service dates is recognised on its invoice date and listed

- **Decision:** A sales line with no service start or end date, or an end before its start, is recognised on its invoice date (no recast effect) and listed as an `undated` line of the period in which it was invoiced. Lines of one invoice number are added up; the threshold applies to the invoice. At or above the threshold (the engagement's query threshold) it needs a query, grouped by Claude with `fin query raise --items`; below it, it is listed only.
- **Why:** Plan §7 D6 and §3 (threshold per compared line). An end before its start is treated as missing dates, not guessed.
- **How to change it:** `reconcile/recast.py:usable` (what counts as usable), `reconcile/base.py:QUERIED_KINDS` (whether `undated` is queried).

## 4. Only the annual periods are recast

- **Decision:** `fin recast run` recasts each annual period of the engagement. The unaudited stub is not recast.
- **Why:** Adjustments take annual periods only (E804), and the stub is never annualised or adjusted. Revenue that a recast moves into the stub months is simply not in any recast period.
- **How to change it:** `reconcile/recast_run.py:run_recast` and `diligence/adjustments.py:propose_in` (the period check).

## 5. The proposed adjustment is a revenue effect with no cost effect

- **Decision:** Each period's difference is proposed as one `recast` adjustment on `revenue`, standalone, with a revenue effect equal to the difference, a cost effect of 0, so the EBITDA effect equals the revenue effect. Costs are not recast.
- **Why:** Plan: "revenue effect and EBITDA effect". fin has no service period for purchases, so it cannot defer related costs; if the user wants a cost effect, they reject and propose their own.
- **How to change it:** `reconcile/recast_run.py:_propose` (the effects tuple).

## 6. fin proposes the adjustment itself, and keeps its own proposals up to date

- **Decision:** `fin recast run` proposes the adjustment (it does not wait for Claude). Running it again on changed data rejects fin's own earlier *proposed* recast adjustment for the period (one whose source reference starts "service_recast") with the reason "superseded by recast run #n" and proposes a new one; an *accepted* one stays and the remaining difference is proposed as another adjustment. A recast adjustment a person proposed by hand is never touched, but counts towards the difference. Only standalone adjustments count (fin proposes standalone; the scopes are kept apart in `adjusted_ebitda`). A recast adjustment the user rejected for the whole difference, or for what the accepted ones leave of it, stands: nothing is proposed again. Only the user accepts or rejects a proposal that fin has not superseded.
- **Why:** "Done when" says the recast "proposes the adjustment". Replacing a stale proposal is not a decision on the data: it keeps G4 from asking the user to decide numbers that are out of date.
- **How to change it:** `reconcile/recast_run.py:_propose`, `reconcile/recast.py:covered`.

## 7. What gate G3 requires

- **Decision:** Once a sales register is loaded, for every annual period of the engagement: the recast ran on the current data; every undated invoice at or above the threshold is in a query (a withdrawn query does not count); the period's difference has a proposed or decided recast adjustment (the proposed and accepted amounts add up to the difference, or the user rejected one for exactly it). No adjustment is needed for a zero difference. Without a loaded sales register G3 has nothing to recast and passes. Contracts are not checked. G3 sits between G2 and G4: G4 cannot be approved before it, and any change to the recast, a query on an undated line or a recast adjustment reopens it.
- **Why:** Plan §5 G3. "Every sales line at or above the threshold has service dates, or is in a query" reads per invoice (decision 3). "Recast ran for every period" is every annual period, because a period with no recast data is a recast of zero.
- **How to change it:** `reconcile/recast_checks.py:checklist`, `diligence/gates.py:GATES`.

## 8. Contracts are Claude-read evidence only

- **Decision:** `fin recast contracts` loads a reading CSV (customer, optional invoice number, start, end, optional value and billing terms, page) with the document's name, sha256 and page, read_by 'claude'. A contract for the invoice (or, without an invoice number, its customer) is named in the note of an undated line, so the query can cite it; it never changes the schedule, the difference, or a gate. Customers are masked (a name is turned into its token, whichever kind first masked it, and never printed), and so are known names in the billing terms. Whether a Claude-read contract is trusted is the user's decision; fin does not verify it.
- **Why:** Plan §7 D6: contracts are "optional evidence ... not the source of the schedule"; CLAUDE.md: Claude does not verify its own reading.
- **How to change it:** `reconcile/contracts.py`; to let a contract supply missing service dates, change `reconcile/recast.py:compute` (the owner would have to decide that).

## 9. Invoices outside the engagement's periods

- **Decision:** An invoice dated before the first period or after the last is "booked" in no period, but the part of its service that falls in a period is recognised there (so an earlier invoice for a service running into FY25 adds revenue to FY25 in the recast).
- **Why:** Revenue recognised in a period is what the service delivers in it, whatever the invoice date; the register may cover more than the engagement.
- **How to change it:** `reconcile/recast.py:compute` (the `inside` test on `booked`).
