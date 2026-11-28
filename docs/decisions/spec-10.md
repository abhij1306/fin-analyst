# SPEC-10 reference documents (PR 1 to 3): builder's readings

Readings, not owner decisions, until the owner confirms them.

## Reference review has its own commands

- **Decision**: reference lines are proposed, confirmed and rejected with `fin reference propose|confirm|reject`, not
  with a new kind of `fin review`. The confirm and reject buttons on the References screen call the same service
  (`fin.services.decisions.decide_reference_lines`), and the chat may not run them.
- **Why**: the spec asks for "the same proposal and review flow, with its own review kind". The flow is the same
  (Claude proposes, the user confirms); the lines live in their own table, so their own commands keep company
  mappings and reference mappings apart and keep `map/commands.py` under the size limit.
- **How to change it**: `src/fin/reference/lines.py`, `src/fin/cli/reference.py`.

## A label the dictionary knows is mapped at once

- **Decision**: a reference label the mapping dictionary maps (same statement, section and label as for a company
  file) is mapped on load (status `dictionary`) and compared at once. Other labels wait for a proposal and the
  user's confirmation.
- **Why**: company figures behave the same way; asking the user to confirm a label fin already knows adds work and
  no safety.
- **How to change it**: `src/fin/reference/store.py` (`_line`).

## Only money is compared, as fin minus the reference

- **Decision**: a line maps to an account of its own statement or to a metric, in rupees; per-share lines and
  ratios are not compared (they wait). The difference is fin's figure minus the reference's.
- **Why**: one unit per figure (engineering rule 2). The sign follows "what fin has against what they say", so a
  query's amount reads as fin's side.
- **How to change it**: `src/fin/reference/lines.py` (`_target`), the view in `src/fin/db/schema/reference.sql`.

## Figures shown in the reference's own unit

- **Decision**: the comparison shows the reference figure, fin's figure and the difference in the unit the
  reference states (`rupees_per_unit`), computed in SQL. Stored values are rupees.
- **Why**: the display unit setting (SPEC-06 PR 1) does not exist yet; the reference's unit is the one the user
  reads beside the databook. When SPEC-06 lands, the comparison should use the display unit.
- **How to change it**: the `*_shown` columns of `reference_comparison`.

## Threshold

- **Decision**: "above the engagement threshold" uses the open engagement's query threshold, else
  `[diligence] query_threshold` in config.toml. A difference at or above it is listed first.
- **Why**: with no engagement open there is still a useful order.
- **How to change it**: `src/fin/reference/compare.py` (`threshold`).

## The hand-offs

- **Decision**: `fin reference query` raises a manual query with the difference as its amount and both sources
  ("fin: <file sheet!cell>; <source>: <file> <sheet!cell>") in its sources. `fin reference adjust` proposes an
  adjustment whose evidence is the same text, with the account when the line maps to one; the assistant states the
  amount and category (a difference is not an adjustment amount by itself). On the screen these are chat buttons:
  the UI never drafts.
- **Why**: criterion 3 asks for both sources; queries and adjustments keep their own approval flow.
- **How to change it**: `src/fin/reference/handoff.py`, `src/fin/ui/templates/_references.html`.

## Text stored as filed

- **Decision**: source, file name and labels are stored and shown as filed.
- **Why**: the masking registry (SPEC-00 PR 5) does not exist yet; fin stores company labels the same way.
- **How to change it**: when the registry lands, register the reference text columns.
