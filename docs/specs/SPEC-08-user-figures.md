# SPEC-08: Figures the user states

## Summary

A P&L in the client run had no finance cost line, so EBITDA could not be computed. On the user's instruction the
assistant inserted zero-value rows into `raw_values` and `facts` with `fin sql --write`, an invented run id and
`read_by = 'claude'` ([obs. 19](../test-run/2026-11-27-client.md)). The figure is the user's statement, and the
data now says an AI read it from a document. This spec adds a command for it and closes the side door.

## Scope

1. **`fin fact state <co> --account A --period P --scope S --value V --reason "..."`** records a figure the user
   states: nil for a line the document does not have, or a value from a source fin cannot hold. It is stored with
   `read_by = 'user'`, the reason, the date and the run id; it never replaces a figure a document gives (that is a
   conflict and becomes a finding). `fin fact unstate` withdraws it. Both back up and log like every data change.
2. **Visible everywhere.** Stated figures carry a mark in statements, evidence pages and the UI, and are listed in
   the report's data coverage section with their reasons.
3. **Metrics that need a missing input say which.** "EBITDA not computed: no finance cost for FY24. Load a document
   that has it, or state it." Today the bridge only stops.
4. **`fin sql --write` refuses `facts` and `raw_values`** (a new error code), as it already refuses the diligence
   tables (E806). Corrections of fin's own reading keep their existing commands.
5. **Diligence.** Inside an engagement a stated figure is allowed (Q3) and listed on gate G1's checklist as an item
   the user sees before approving.

Out of scope: editing a figure a document gives; bulk entry from a file.

## Acceptance criteria

- [ ] A test states a nil, sees the metric compute, and finds the figure in the data coverage list with its reason.
- [ ] A test shows `fin sql --write` into `facts` is refused with the new code, and the code is in `errors.md`.
- [ ] Stating a value for an account a loaded document already gives creates a finding and changes no figure.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add fin fact state for figures the user states | `src/fin/facts`, schema, CLI, views, docs | criteria 1, 3 |
| 2 | Refuse direct writes to facts and name the missing input of a metric | `src/fin/cli` sql, `src/fin/metrics`, `errors.md` | criterion 2 |
| 3 | Mark stated figures in statements, evidence, reports and gate G1 | `src/fin/report`, `validate/gates.py`, UI | tests green |

Risk: PR 1 and PR 3 touch gates. They get a separate reviewer.
