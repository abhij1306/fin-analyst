# FDD cross-PR review fixes

Fixes from the FDD cross-PR review (2026-11-26): calculations, masking and gates (#105), the report and docs (#104).
Each entry that is not a plain bug fix is the orchestrator's reading, owner may override; none is an owner decision.

# Calculations, masking, gates (#105)

## Lock-in months left counts whole months

- **Decision**: `contract_coverage.lock_in_left_months` is the whole months from the period end to the end of the
  lock-in (`date_sub('month', ...)`), so a lock-in ending the day after the period end has 0 months left.
- **Why**: a bug fix: the column was documented as whole months but counted month boundaries (`date_diff`), which
  overstated it by up to a month.
- **How to change it**: `db/schema/fdd_contracts.sql`: `contract_coverage`.

## An order whose term is over is not backlog

- **Decision**: an orderbook order whose end date is before the day after the as-at date adds 0 to
  `remaining_amount` (still to come) as well as to the next year ("term over"). `fin visibility` and the FDD report
  give the orderbook's as-at date and file, and say when the as-at date is not the last period end (the unbilled value
  is then what was still to come on the as-at date, spread from the day after it).
- **Why**: the unbilled value of an ended order is not revenue still to come; summing it overstated the backlog. The
  as-at date was not shown, so a stub-date orderbook read as if it were at the period end. Orchestrator's reading,
  owner may override.
- **How to change it**: `db/schema/fdd_orderbook.sql`: `backlog_lines`; `src/fin/reconcile/visibility.py`:
  `visibility`, `orderbook_date_note`.

## Reasons and FDD text are masked, at write and when a name is learned later

- **Superseded 2026-11-26** by [masking.md](masking.md): reasons and FDD text are stored as written.

- **Decision**: the reasons stored with provision ledger, net debt class, tax class and related party link proposals
  are masked when written, and `FREE_TEXT_COLUMNS` adds `netdebt_items` (description, evidence, reason),
  `netdebt_classes.reason`, `provision_ledgers.reason`, `tax_ledgers.reason`, `related_party_links.reason`,
  `disclosures.forum` and `stage`, so a name learned from a later file is masked in them too. The final response
  snapshot and the query answer it copies (`accountant_queries.answer`, masked when written) are both masked again,
  so they stay equal and a final finding is not flagged as changed by masking alone.
- **Why**: the diligence rule is that names are masked in everything fin prints; these columns are printed.
- **How to change it**: `src/fin/diligence/mask.py`: `FREE_TEXT_COLUMNS`; the `propose` functions.

## Proposals name ledgers of the latest engagement only

- **Decision**: `fin provisions propose` and `fin netdebt propose` accept only ledgers of the latest engagement's
  loaded trial balances (E920, E930 otherwise).
- **Why**: the views that use them read the latest engagement only; a ledger from an earlier engagement would sit
  unused.
- **How to change it**: `src/fin/diligence/provisions.py`, `src/fin/diligence/netdebt.py`: `propose`.

## A final finding is a snapshot

- **Decision**: when the user makes a finding final, fin copies its amount, amount source, link status (of the
  adjustment or net debt item, else of the query) and management response into `fdd_findings.final_*`. A final
  finding shows the snapshot in `fin fdd finding list` and the FDD report; when the live values differ it is flagged
  "changed since final" with the values now. Any finding whose linked adjustment or net debt item is rejected, or whose
  linked query is withdrawn, is flagged. Returning it to draft clears the snapshot; the user makes it final again to
  sign off the new values.
- **Why**: what the user signed off should not change under them, and a change should be visible rather than silent.
  Orchestrator's reading, owner may override (the alternatives: live values with a flag only, or refusing changes to
  evidence linked to a final finding).
- **How to change it**: `db/schema/fdd_findings.sql`: `fdd_findings`, `fdd_finding_rows`; `src/fin/fdd/findings.py`:
  `_snapshot`, `flags`; `src/fin/report/fdd_tables.py`: `_finding`.

## G4 covers the FDD proposals

- **Decision**: G4 Adjustments also needs no proposed net debt item, no draft FDD finding and no proposed net debt
  class, tax class or related party link, and those five tables are among its inputs, so a new proposal, a decision or
  a changed finding reopens an approved G4 (and `fin engagement close` refuses until the user approves it again).
  Other FDD data (provision ledgers, contracts, disclosures, samples and walkthroughs, orderbook, GST returns) has no
  checklist line; what of it feeds a reconciliation is under G2 through that reconciliation (docs/diligence.md).
- **Why**: these are user decisions the report depends on; closing an engagement with them pending would close it on
  undecided data. Provision ledgers are left out to keep to the review's list. Orchestrator's reading, owner may
  override (the alternative was documenting that FDD data sits outside the gates).
- **How to change it**: `src/fin/fdd/gate_checks.py`: `_CHECKS`; `src/fin/diligence/gates.py`: G4.

## Cut-off reads the service end date

- **Decision**: "billed late" (sales) and the purchase unrecorded-liability test flag an invoice dated after the year
  end only when its service ended on or before the year end. An invoice dated after the year end for a service that
  started on or before it and ends after it "straddles the year end": it is its own line (key suffix "(straddles)"
  when the same invoice also has lines flagged in full), and its amount is the part for the service days up to the
  year end, by days, computed in SQL. An invoice dated after the year end for a service that started by it and has
  no service end is still flagged in full, its note saying the end is not known. "Billed ahead" stays on service start after the year end.
- **Why**: an invoice billed in arrears for a service running across the year end is normal and only partly belongs to
  the earlier year; flagging it in full put noise above the threshold that G2 forces into queries. A line without a
  service end cannot be shown to be performed after the year end, so it stays flagged rather than hidden.
  Orchestrator's reading, owner may override.
- **How to change it**: `src/fin/reconcile/cutoff.py`: `_flag`, `_SELECT`, `_key`.

## Other bank balances wait for the user

- **Decision**: a ledger confirmed to `other_bank_balances` has no default net debt class; it stays unclassified (and
  counted as such beside net debt) until the user confirms a class.
- **Why**: other bank balances are often fixed deposits, margin money or lien-marked balances that a buyer treats
  apart from free cash; defaulting them to cash lowered net debt without the user's word. Orchestrator's reading,
  owner may override. [Client question 4](../client-questions.md).
- **How to change it**: `db/schema/fdd_netdebt.sql`: `netdebt_default_accounts`.

# Report and docs (#104)

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
  names, the G2 bullet) were brought in line with the code. The G2 bullet also says which FDD data is under G2 (through
  the reconciliations) and points to G4 for the FDD proposals (below).
- **Judging error**: E970 and E972 name the phrase that matched ("matched: 'enterprise value'"), so the user can see
  why ordinary wording was refused. The rule itself is unchanged.
