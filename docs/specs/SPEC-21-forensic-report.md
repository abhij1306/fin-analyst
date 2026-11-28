# SPEC-21: Forensic section in the FDD report

## Summary

The FDD report (`fin report --fdd`, [fdd.md](../fdd.md) F8) has no place for forensic work. This spec adds a forensic
section and a `forensic` area for FDD findings, so what the tests of SPEC-18 to 20 found reaches the report through
the user's decisions (the owner's plan, X6; slides are backlog).

## Scope

1. **Area `forensic`** for `fdd_findings.area`. `fin fdd finding propose <co> --area forensic --flags <ids>` links
   the flags a finding rests on as evidence (a link table); its amount comes from the existing links (a query raised
   on those flags, for example), never summed from flags, since one entry can be flagged by two tests. The Forensic
   screen's "to a finding" click (SPEC-18) opens this draft. Only the user marks a finding final.
2. **Report section "Forensic tests"**, before "Reconciliations and queries":
   - every test: ran (date, population, flags, flags at or above the threshold), stale, too few entries, or cannot
     run and what it needs; no score, rating or "risk" word;
   - per test, the flags at or above the threshold ordered by amount, at most 20, with the rest counted (all of them
     in `fin forensic flags`); each with its source (file and sheet!row or page);
   - the queries raised on flags with their status, and the final forensic findings (drafts only with `--drafts`);
   - with no forensic run, the section says so; it is never left out.
3. **Language checks.** The section and forensic findings go through the E970 and E972 checks: no valuation,
   investment rating or buy/don't-buy language.
4. **Planted cases.** `mock/EXPECTED.md` gains the section's expected content for the mock set: the tests, their
   counts and the planted flags.
5. **Docs.** `docs/fdd.md` (F8 areas), `docs/product.md` (the report section, the new option on the coverage table's
   existing row).

Out of scope: slides (pptx, backlog); an Excel export sheet of flags; any summary score.

## Acceptance criteria

- [ ] On the mock set with the tests run, the section lists every forensic test with the counts `mock/EXPECTED.md`
      gives and the planted flags in amount order.
- [ ] A draft forensic finding is absent without `--drafts` and present with it; a final one is present.
- [ ] A finding text with a rating word is refused (E970); the report refuses one in the narrative (E972).
- [ ] A stale run is shown as stale in the section, not as current.
- [ ] Party names in the section are tokens; ledger names as filed (the masking registry test).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add the forensic area and the forensic section of the FDD report | `fdd_findings.sql`, `src/fin/fdd`, `templates/report/fdd.qmd`, `mock/EXPECTED.md`, docs | all criteria |

Risk: high (a schema file, findings the user decides). Depends on SPEC-18, 19 and 20.
