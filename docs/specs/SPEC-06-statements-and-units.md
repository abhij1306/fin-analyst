# SPEC-06: Statements, display units and layout

## Summary

The client's first request after the first answer was: show everything in INR million, laid out like a databook
([obs. 11](../test-run/2026-11-27-client.md)). fin stores rupees and has no display setting, so the assistant divided
in every query and kept the preference in its own memory. This spec makes the display unit and the statement layout
settings of fin, used by the CLI, the UI, exports and reports alike, and adds a Statements screen. The client
confirmed it on 2026-11-27: asked whether amounts should print in lakh/crore grouping, the answer was "INR Mn"
([client-answers.md](../decisions/client-answers.md), answer 14), so Indian digit grouping is not built.

## Scope

1. **Display settings** in `config.toml [display]`, with a per-company override: `unit` (rupees, thousands, lakhs,
   millions, crores), `decimals`, `negatives` (minus or brackets), `unit_label` (for example "Rs m"). Stored
   values stay in rupees. Conversion and rounding happen in SQL (a `display_value` function or view), never in
   prose or templates, and every table says its unit in its caption.
2. **Databook layout** for statements: line items as rows, periods as columns, subtotals marked, optional
   "% of revenue" and year-on-year columns. `fin statements <co> --layout databook`.
3. **Statements screen.** Company, scope, statement, periods and unit pickers; the table; each figure links to its
   source (file, sheet and cell or page) and shows "unconfirmed mapping" or "read by AI" marks; download as Excel.
4. **Reports and evidence pages** use the display settings. Figures in narrative stay inline references.
5. **Rounding.** A total is the rounded total, not the sum of rounded lines; a table whose lines do not add because
   of rounding says so in a footnote.

Out of scope: currencies other than INR; restating history.

## Acceptance criteria

- [ ] One setting changes the unit in `fin statements`, the Statements screen, the Excel export and the report;
      a test renders all four for a fixture and compares the same cell.
- [ ] No Python or template code divides a stored value: a test searches for it.
- [ ] The databook layout of the synthetic company matches a checked-in expected table.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add display unit settings and convert in SQL | `config.py`, `company_views.sql`, `src/fin/output` | criterion 2 |
| 2 | Add the databook layout to statements and the Excel export | `src/fin/statements.py`, `metrics/export.py` | criterion 3 |
| 3 | Add the Statements screen | `src/fin/ui` | UI test |
| 4 | Use the display settings in reports and evidence pages | `src/fin/report`, evidence | criterion 1 |
