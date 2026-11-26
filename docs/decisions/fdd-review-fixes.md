# FDD cross-PR review fixes: report and docs

The review is in the orchestrator's notes (2026-11-26). The items below are the report and docs half (M5, M6, L5,
L6). Readings marked "orchestrator's reading, owner may override" are not owner decisions.

## The FDD report shows the data F1, F5 and F6 already collect (M5)

- **Decision**: `fin report --fdd` shows, with one table helper each: in the contracts table, lock-in left (months),
  notice period (days) and auto-renew ("not read" when the reading left it empty); in the walkthroughs table, the
  count of lines with an exception, and under it one row per recorded exception (sample line, party, invoice, amount,
  check, evidence, file + page; labelled as read by Claude, not verified); in the tax table, net deferred tax, its
  movement in the year and the MAT credit (standalone); a new "GST returns as filed" table (months, taxable value,
  output tax, input tax credit, reverse charge, paid in cash and from credit, per period and return); in the related
  parties section, the pricing against other customers and vendors (rate per unit, difference) and the loans (opening,
  closing, interest, implied rate). Orchestrator's reading, owner may override: these are shown as the figures fin
  computed, with no wording that judges them; the FDD exception already allows findings to carry severity.
- **Why**: the plan items "pricing and loans", "deferred tax movement" and "MAT credit" were built but invisible in
  F8; the alternative was dropping them from the data functions, which is a loss of evidence for findings.
- **Rates**: a rate per unit is printed in rupees whatever the report's display unit (a crore-scaled rate would be
  meaningless).
- **How to change it**: `report/fdd_tables.py`, `report/fdd_data.py` (`walkthrough_exceptions` from
  `sampling/walkthrough.exception_lines`), `templates/report/fdd.qmd`.

## The /diligence skill has a step per FDD workstream (M6)

- **Decision**: steps 7d to 7h of `.claude/commands/diligence.md` cover contracts coverage, samples and walkthroughs
  (F1), inquiries (F2), disclosures and related parties (F5), GST and tax (F6), orderbook and visibility (F7), each with
  its user-decides rule (confirm tax classes, confirm related links, approve and send inquiries, trust of readings).
  Step 7a lists the contract reading columns including party_kind and the terms. Orchestrator's reading, owner may
  override: the wording of each user-decides rule follows the existing steps (the user confirms, Claude proposes).

## Stale text and the judging error (L5, L6)

- **Decision**: errors.py header and E806 docstring, the E806 row and E9xx row order in errors.md, the E806 list in
  `query/write.py`'s docstring, development.md (error range, net debt text) and diligence.md (ingest kinds, reconcile
  names, the G2 bullet) were brought in line with the code. The G2 bullet also states that FDD data outside the
  reconciliations is under no gate (the review's M3 is PR 1's; this only describes what is built today).
- **Judging error**: E970 and E972 name the phrase that matched ("matched: 'enterprise value'"), so the user can see
  why ordinary wording was refused. The rule itself is unchanged.
