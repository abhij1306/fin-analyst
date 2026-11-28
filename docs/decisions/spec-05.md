# SPEC-05: mapping review and dictionary coverage

Builder's readings (2026-11-28), for the owner to confirm or change.

## The low-confidence rule lives in the confirm step, for the CLI and the UI

- **Decision**: `fin review confirm` with several ids confirms those at confidence 0.8 or above and skips the
  lower ones, naming each ("#2 not confirmed: confidence 0.6 is below 0.8; ...") with an E302 warning event. When
  every id named is low, it refuses with E304 and confirms nothing. A low id named alone, or any ids with `--each`,
  is confirmed, one step per id. An item with no confidence (a new account the user typed) is not low.
- **Why**: Q4 says a low proposal is confirmed on its own. Skipping and saying which, rather than refusing the
  whole command, lets the user confirm the safe ones in one go, which is what the client did in the run. The
  UI's bulk button sends only the ids at 0.8 or above, and the rule is in one place
  (`src/fin/map/review_confirm.py: confirm`), so a button cannot get round it.
- **How to change it**: `BULK_CONFIDENCE` and `_split_low` in `src/fin/map/review_confirm.py`. The 0.7 threshold of
  the E302 warning on `fin map propose` (`review.LOW_CONFIDENCE`) is unchanged.

## A mapping change is applied to the loaded facts and resolves findings

- **Decision**: `fin map set` (and the UI's "Change the mapping") applies the company mapping to the facts already
  loaded or held, in one transaction, and checks those documents again. A finding the checks no longer find
  becomes `resolved`, as after `fin review confirm`; there is no ingest run.
- **Why**: the spec says "supersedes their findings". `superseded` already means "a newer version of the file
  replaced it" (`fin ingest --force`); a finding that no longer holds after a fix is `resolved` everywhere else.
  Using the same word keeps `fin findings list` and the audit table readable.
- **How to change it**: `src/fin/services/decisions.py: _set_mapping`.

## Which mappings a finding names

- **Decision**: a finding names every mapping made in review or with `fin map set` (`label_map.source` not
  `seed`) behind a line of the same document, statement, period and scope as the figures it compares. Seed
  mappings are not named.
- **Why**: a line mapped to the wrong account has left the subtotal it belongs to, so looking only at the compared
  lines would miss the cause the client met. Unconfirmed lines never enter a subtotal: a statement with
  unconfirmed lines does not run its subtotal check at all (it is listed under "checks that did not run" instead),
  so there is no unconfirmed line to name.
- **How to change it**: `src/fin/validate/finding_rows.py: _mappings`.

## Changing a mapping from a finding is `fin map set`

- **Decision**: the UI's "Change the mapping" takes the mapping id the finding names and runs `fin map set` with
  the statement, section and label as the dictionary stores them (normalized), so the run record is the one the
  CLI would leave. It maps the label for this company only.
- **Why**: one service for both interfaces (SPEC-02), and a change made for one company must not move another
  company's figures.
- **How to change it**: `src/fin/services/decisions.py: change_mapping`.

## Checks that did not run are stored per document

- **Decision**: the company table `checks_not_run` holds, per loaded or held document, each check that did not
  run, why, and what would make it run. It is replaced every time the document is checked. `fin status` and the
  Home screen list it.
- **Why**: the warning events were already written but only visible in the log. A table that is rebuilt on each
  check never goes stale.
- **How to change it**: `src/fin/validate/not_run.py`.

## Dictionary labels from the run

- **Decision**: the seed gets the standard indirect-method cash flow labels in their usual printed forms
  (working-capital movements in both "(Increase)/decrease" and "Decrease/(increase)" forms, the activity totals,
  cash generated from operations, taxes paid, opening and closing cash, capex including capital work in
  progress) and two P&L forms of prior-year tax. Section headings ("Adjustments for:") and ambiguous labels
  ("Total") are left to review. The criterion's fixture (`tests/fixtures/dictionary/indirect_cash_flow.csv`) is
  synthetic, written from the usual printed forms, not copied from the client's files.
- **Why**: the client's files stay out of the public repo. Cash flow lines are stored with the printed sign, so
  each "increase/decrease" form maps to the same account.
- **How to change it**: `templates/dictionary/labels.csv`; `fin init` adds new rows to an existing data folder.
