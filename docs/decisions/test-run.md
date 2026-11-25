# Decisions: test-run (end-to-end run on synthetic data, 2026-11-25)

Builder readings, not owner decisions. Owner source for the workstream: the orchestrator's brief of 2026-11-25 (an
end-to-end test run on synthetic data before the client runs fin on real documents; fix bugs, improve the eval kit).
The run itself: `docs/test-run/2026-11-25.md`.

## Stale reconciliation drafts are listed, never withdrawn by fin

- **Decision:** When `fin reconcile <co> tb_vs_statements` runs again and a draft query of an earlier run has no line
  in the new run (a ledger was remapped, a figure re-read), the command lists it as a stale draft and says how to
  withdraw it. fin does not withdraw it. While a ledger is unmapped no line is checked against earlier queries, so
  nothing is listed as stale.
- **Why:** Withdrawing a query is the user's call (CLAUDE.md, Diligence). In the run, mapping a cash ledger to `cash`
  rather than the statements' `cash_and_bank_balances` left four large drafts that no later run mentioned; nothing
  told Claude or the user they were orphaned.
- **How to change it:** `src/fin/diligence/recon.py: _stale_drafts` (and `format_result` for the wording).

## A query raised from a bank finding takes its statement's engagement period

- **Decision:** A bank overlap or gap finding stores the statements' dates as its period. A query raised from it takes
  the engagement period its statement was loaded into (`source_files.period`).
- **Why:** The query carried a date range that is not a period of the engagement, so it fell out of every per-period
  list, export and gate check.
- **How to change it:** `src/fin/diligence/queries.py: raise_from_finding`.

## A manual query's period must be a year of the engagement or its stub (E881)

- **Decision:** `fin query raise --amount --period P` refuses a period the open engagement does not have.
- **Why:** A typo (`FY52`) raised a query no gate or export would ever show.
- **How to change it:** `src/fin/diligence/queries.py: raise_manual` (`periods_of`).

## Unknown tokens are refused when a query is raised or edited, not only at export

- **Decision:** A token (`CUST-0001`, ...) in a query's wording that is not in `mask_values` is E861 at raise and
  edit, as it already was at export.
- **Why:** The mistake showed only after the user had approved the wording; the draft is the place to fix it.
- **How to change it:** `src/fin/diligence/queries.py: check_tokens`.

## Recast months are rounded once, on the running total

- **Decision:** A sales line's amount is spread over months by service day; each month's share is the rounded running
  total minus the rounded running total before it.
- **Why:** Rounding each month and giving the last month the remainder put all the rounding error on the last month,
  so a person's check of an even split (a year's fee of Rs 5,00,00,000: Rs 41,66,666.67 a month) did not match fin.
- **How to change it:** `src/fin/reconcile/recast.py: spread`.

## Golden `query_issue` is compared without sign; relative difference to six significant digits

- **Decision:** `query_issue` scores the absolute amount of the query (as `docs/client-handoff.md` says); the relative
  difference is kept to six significant digits and the bundle shows a tiny one with two significant digits.
- **Why:** A credit-side query scored as a fail against a person's unsigned figure; a misread digit in a figure of
  crores showed as "-0.00%" in the bundle, which reads as no difference.
- **How to change it:** `src/fin/evals/score.py: relative` and the `query_issue` entry of its SQL table;
  `src/fin/evals/bundle.py: _percent`.

## A booked receipt is a foreign receipt's rupee value only at a rate within 10% of the invoice's

- **Decision:** For a matched foreign-currency group, the booked receipt is the customer's nearest free Receipt voucher
  within `fx_window_days` whose rupees are within 10% (`FX_RATE_BAND`) of the receipt's amount at the invoice's rate.
  A voucher further off is set aside: the match gets the note "no booked value" naming it, and no fx line is made. An
  fx line names the voucher it used.
- **Why:** The lookup used customer and date only. In the run the USD 2,000 receipt (Rs 1,60,000 at the invoice's
  rate) was never booked; the only voucher near it was Rs 2,00,000 into an INR account, another receipt, and fin
  reported a false fx gain of Rs 40,000. The window, the tie-break and reuse were not the cause (each voucher is used
  once). A real rupee move within a year is well inside 10%; a different receipt rarely is.
- **How to change it:** `src/fin/reconcile/bank_lines.py: FX_RATE_BAND` and `_Booking.find`.

## Bank continuity findings name each file once

- **Decision:** Overlap and gap findings keep the file names in `file_name`/`other_file` and the place in the
  sources ("opening balance", "starts 2024-06-01"), so a query's sources read "may.xlsx opening balance; apr.xlsx
  closing balance".
- **Why:** The sources printed each file name twice.
- **How to change it:** `src/fin/diligence/bank_checks.py: consistency_findings`.

## Open for the owner: a query G3 asks for reopens the approved G2

Not decided; gate rules unchanged in this PR.

- **Finding:** G3 requires every undated sales invoice at or above the threshold to be in a query
  (`fin query raise <co> --items`). G2's checklist is "no query in draft" and its inputs are every query's id, wording
  and draft state (`src/fin/diligence/gates.py`, `_queries_checklist`, `_queries_inputs`). So the query G3 asks for
  reopens an approved G2. Approving the wording passes G2's checklist again, but its inputs have changed, so the owner
  approves G2 a second time. Until then `fin adjust accept|reject`, which G4 needs, refuses (E801). The same happens
  for any query raised after G2, for example on a bank finding.
- **Options:**
  1. **G2 checks only the queries for its own checks.** G2's "no draft" line and inputs cover queries on
     reconciliation lines and findings, not those on `service_recast` lines (found through `query_items` ->
     `recon_items` -> `recon_runs.name`). G3 adds "no draft among its undated-invoice queries"; its inputs already
     hold those queries.
  2. **Recast queries are raised before G2.** Change nothing in fin. The /diligence skill runs `fin recast run` and
     raises its queries before preparing G2. Cheapest, but G2 still reopens for any later query and the gate order no
     longer matches the work order.
  3. **G2 ignores queries raised after its approval** unless they are on reconciliation lines. This is close to option 1,
     but depends on time instead of what the query is about, so it is harder to explain.
- **Recommendation:** option 1. Each gate then reviews the wording of the queries its own checks ask for, and re-approving
  G2 after a recast is no longer needed. A manual query on a reconciliation difference raised after G2 still reopens G2,
  which is right.
- **How to change it:** `src/fin/diligence/gates.py: _queries_checklist, _queries_inputs` (exclude queries whose
  `query_items` lead to a `service_recast` run); `src/fin/reconcile/recast_checks.py` (a no-draft line). Option 2:
  `.claude/commands/diligence.md` only.
