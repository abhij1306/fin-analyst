# SPEC-10: Reference documents

## Summary

The client holds a banker's databook and an advisor's FDD databook for the same company and checks fin against them
([obs. 16](../test-run/2026-11-27-client.md)). Today the assistant opens them with ad-hoc Python outside fin, and a
difference it notices is said in chat and recorded nowhere. This spec lets fin hold a third party's figures beside
the company's and show the differences. Those figures never become company data (Q5).

## Scope

1. **`fin reference add <co> <file> --source "name" --unit ...`** stores a third-party table: source name, sheet and
   cell, label, period, value. It goes through the same archive and audit path as a document, into its own tables.
   Reading is the same as for company files: the existing reader, then an assistant reading.
2. **Mapping** reference labels to accounts or metrics uses the same proposal and review flow as SPEC-05, with its
   own review kind.
3. **Comparison.** `fin reference compare <co>` and a screen: for each mapped line and period, fin's figure, the
   reference figure, the difference, both sources, in the display unit. Differences above the engagement threshold
   are listed first.
4. **From a difference, one step** to draft a query (`fin query raise`) or propose an adjustment with both sources
   as evidence. Nothing is changed to make the figures tie.

Out of scope: treating a reference as a source of facts; reconciling two references with each other.

## Acceptance criteria

- [ ] A synthetic reference workbook with three planted differences loads; the comparison lists exactly those three.
- [ ] No reference value appears in `current_facts`, `metrics` or a report's figures: a test checks.
- [ ] A query drafted from a difference carries both sources.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Store third-party reference tables apart from company facts | schema, `src/fin/reference`, CLI, docs | criterion 2 |
| 2 | Map reference lines and compare them with fin's figures | `src/fin/reference`, review kind | criterion 1 |
| 3 | Add the comparison screen and the query and adjustment hand-offs | `src/fin/ui` | criterion 3 |
