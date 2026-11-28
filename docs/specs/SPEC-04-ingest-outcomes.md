# SPEC-04: Ingest outcomes on real documents

## Summary

No file in the client run loaded without a Claude reading
([obs. 4](../test-run/2026-11-27-client.md)). `CLAUDE.md` says extraction stays simple: no parsing heuristics, no
layout special cases. This spec keeps that rule (Q1 in [README](README.md)) and removes the failures that are not
about parsing: a missing unit line, an unsupported file type, and no add-on chosen.

## Scope

1. **Missing unit line is a question, not a failure.** A sheet or page with readable statements and no unit line is
   held with the question "which unit?". The answer (`fin ingest --unit lakhs|crores|millions|thousands|rupees`, or
   the choice in the UI) is stored on the document with who gave it, and shown wherever its figures are sourced.
   fin does not guess a unit from the size of the numbers.
2. **`.xls` files** are read like `.xlsx` (Q2: new dependency `xlrd`, read-only). `.xlsm` already opens;
   macros are never run.
3. **Add-on offered at the point of need.** When pages are skipped and `parser = "none"`, the outcome names the
   add-ons with their size and whether data leaves the machine, and the UI offers to install one (a job).
4. **Values with no label (E204)** names the cells, and holds the sheet instead of failing the whole file when other
   sheets are readable.
5. **Synthetic fixtures** that reproduce each failure of the client run: the unit sentence without a unit word, a
   statement with no unit line, a scanned page, an `.xls` workbook, a row of values with no label. They join the
   eval checks ([eval-kinds.md](../eval-kinds.md)).

Out of scope: reading layouts pdfplumber and the chosen add-on cannot read; that stays with the assistant's reading.

## Acceptance criteria

- [ ] Each new fixture has a test with its expected outcome (loaded, or held with a named question).
- [ ] A document whose unit the user gave shows "unit given by user" in `fin audit` and on its evidence page.
- [ ] Nothing here adds a layout rule to `src/fin/extract`; the diff there is the unit question, the `.xls`
      reader and the E204 change only.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Hold a document with no unit line and ask for the unit | `src/fin/extract/units.py`, `ingest.py`, schema, docs | criteria 1, 2 |
| 2 | Read .xls workbooks | `src/fin/extract/excel.py`, `pyproject.toml` | fixture loads |
| 3 | Hold only the sheet with unlabeled values and name the cells | `src/fin/extract` | fixture test |
| 4 | Offer a PDF add-on when pages are skipped | outcome text, UI job | UI test |
