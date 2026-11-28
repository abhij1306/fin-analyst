# SPEC-04: ingest outcomes

Builder readings of [../specs/SPEC-04-ingest-outcomes.md](../specs/SPEC-04-ingest-outcomes.md), rows 1 to 4 built in
one PR. The owner confirms or changes them. Format: [README.md](README.md).

## 1. Who gave the unit is shown in the unit text

- **Decision**: each raw value read with a given unit records `unit given by user` as its unit text, or `unit given
  by user, entered by Claude` when Claude Code ran `fin ingest --unit`. `documents.unit_given` and
  `unit_given_by` keep the answer and the actor.
- **Why**: `fin audit` and the evidence page already show the unit text, so the acceptance line holds with no change
  to them; the user is the source of the answer even when Claude typed it.
- **How to change it**: `src/fin/ingest_read.py` (`GivenUnit.text`).

## 2. The whole document is held for the unit

- **Decision**: a document with any statement sheet or page without a unit line loads nothing; it is counted as
  held and goes to `review/` with the question and the answers. Its other sheets do not load first.
- **Why**: loading part of a document and the rest later makes two versions of one file; the answer is one word.
- **How to change it**: `src/fin/ingest.py` (`_held_for_unit`), `src/fin/extract/excel.py` (`_which_unit`).

## 3. A unit nothing needed is refused

- **Decision**: `--unit` on a document where every sheet and page has its own unit line is E207, nothing loaded.
- **Why**: a given unit that is silently ignored hides a wrong answer; rule 15 (no silent defaults).
- **How to change it**: `src/fin/ingest_read.py` (`read_document`), `src/fin/audit/errors.py` (`UnitNotUsed`).

## 4. Only the sheet with unlabeled figures is held

- **Decision**: in a workbook only E204 "values with no label" holds just its sheet; the other E204 cases and other
  codes are unchanged. A workbook with no other statement sheet fails with E204 as before.
- **Why**: the spec names this case; the other cases already have their outcomes.
- **How to change it**: `src/fin/extract/excel.py` (`_sheet_values`, `HeldSheet`).

## 5. The add-on offer sits on the Files screen as a small card

- **Decision**: one card above Loaded documents with an Install button per add-on, shown while a loaded document or a
  failed file in `review/` has skipped PDF pages and `[pdf] parser` is `none`. Install is a background job.
- **Why**: SPEC-03 is reworking the Files screen; one include line keeps the merge small.
- **How to change it**: `src/fin/ui/templates/_addon_offer.html`, `src/fin/services/addon.py` (`offer`).

## 6. The answer words

- **Decision**: `--unit lakhs|crores|millions|thousands|rupees`, stored as fin's unit names (`lakh`, `crore`, ...).
- **Why**: the plural is how a company writes the unit line.
- **How to change it**: `src/fin/ingest_read.py` (`UNIT_ANSWERS`).

## 7. No unit choice in the UI yet

- **Decision**: the UI has no unit question form; `ingest_file` takes `unit`, so SPEC-03's ingest job can pass it.
- **Why**: SPEC-03 builds ingesting from the UI; a form here would be replaced there.
- **How to change it**: `src/fin/ingest.py` (`ingest_file`).
