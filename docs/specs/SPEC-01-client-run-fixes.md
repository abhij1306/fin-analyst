# SPEC-01: Fixes from the first client run

## Summary

Five small defects the first client run hit ([obs. 5, 13, 17, 18](../test-run/2026-11-27-client.md)). Each is
reproduced with a synthetic fixture first, then fixed. One PR.

## Scope

1. **"in INR" unit line.** "All amounts in INR, unless otherwise stated", with no unit word after INR, means rupees.
   The client's Claude made this change locally; it is not in the repo.
2. **`fin sql --evidence` crash.** A query whose evidence columns hold a NULL ended in a `TypeError` with no error
   code. It must give evidence for the rows that have a source and name the rows that do not.
3. **Negative amounts.** `fin adjust propose --revenue-effect -1000000` is read as an unknown flag. Negative
   values work after every amount option, in both forms (`--opt -1` and `--opt=-1`).
4. **Listing with no engagement.** `fin adjust list` and `fin query list` print "no open engagement" and exit 0.
   Commands that change something keep E803.
5. **Unexpected errors.** An exception without an error class is logged today with no code. It gets one new code
   for "unexpected error", with the traceback in the run log; the user sees the code and the run id. (E012 and the
   traceback are logged since 2026-11-28; what is left is showing the code and run id instead of a raw traceback.)

Out of scope: anything that needs a new command or screen.

## Acceptance criteria

- [ ] A fixture with the unit line of item 1 loads in rupees.
- [ ] Items 2 to 5 each have a test that fails before the fix.
- [ ] `docs/product.md` and `docs/errors.md` say what changed.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Fix the unit line, evidence crash, negative amounts and empty lists from the client run | `src/fin/extract/units.py`, `src/fin/cli`, `src/fin/diligence/cli.py`, evidence | criteria met |
