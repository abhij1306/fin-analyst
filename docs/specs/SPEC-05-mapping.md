# SPEC-05: Mapping review and dictionary coverage

## Summary

In the client run 40 labels had no mapping (29 on the cash flow statement), all 40 proposals were confirmed in one
command including four at low confidence, and one mapping produced four findings that looked like the company's
error ([obs. 7 to 10](../test-run/2026-11-27-client.md)). The Mappings screen can only accept a proposal. This spec
makes mapping review a complete screen and fills the dictionary's cash-flow gap.

## Scope

1. **Dictionary seed.** Add the standard indirect-method cash flow labels (working-capital movements with their
   "Increase / (Decrease)" forms, the three activity totals, cash generated from operations, opening and closing
   cash) and the handful of P&L and balance sheet labels from the run. Follow "Add an account or a label" in
   [development.md](../development.md), including sign conventions.
2. **Mappings screen.**
   - Each pending label shows the statement, section, a sample value, the proposed account, confidence and reason.
   - Accept, choose another account (search over the dictionary), or reject.
   - Bulk accept only at confidence 0.8 or above (Q4); lower ones are confirmed one at a time. The CLI gets the same
     rule: `fin review confirm` refuses a low-confidence id unless it is named alone or with `--each`.
   - The other review kinds (ledgers, customers, tax, related parties, net debt, matches) get the same list.
3. **A finding that comes from a mapping says so.** When a subtotal check fails and one of its lines was mapped in
   this session or is unconfirmed, the finding names that mapping and offers "change the mapping" beside "save as is".
4. **Rechecking after a mapping change** needs no re-ingest: changing a mapping re-runs the checks for the documents
   it touches and supersedes their findings.
5. **Checks that did not run** are listed with what would make them run ("confirm a cash figure for FY24").

Out of scope: automatic mapping beyond the dictionary; who proposes mappings (SPEC-16).

## Acceptance criteria

- [ ] The client-run cash flow labels, rebuilt as a synthetic fixture, load with no unmapped label.
- [ ] A test confirms that a bulk confirm skips low-confidence ids and says which.
- [ ] Changing a mapping in the UI clears a finding it caused, with no ingest run in the log.
- [ ] Every review kind the CLI has is on the screen.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add the indirect cash flow labels to the dictionary seed | `templates/dictionary`, fixtures | criterion 1 |
| 2 | Require low-confidence mappings to be confirmed one at a time | `src/fin/map/review.py`, services, docs | criterion 2 |
| 3 | Recheck after a mapping change and name the mapping behind a finding | `src/fin/map`, `src/fin/validate` | criterion 3 |
| 4 | Complete the Mappings screen: choose an account, bulk rules, all review kinds | `src/fin/ui` | criterion 4 |
| 5 | List the checks that did not run and what they need | `src/fin/validate`, status, UI | UI test |
