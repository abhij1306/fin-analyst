---
description: Ingest financial files for one company, then propose mappings for unmapped labels
argument-hint: <company> [path]
---

Ingest documents for the company in `$ARGUMENTS`.

1. Run `uv run fin ingest $ARGUMENTS`. Report the per-file outcomes and the summary line (loaded, skipped, failed, pending review, run_id). If a command fails, report its error code and run_id and stop. Do not work around it.
2. If anything is pending review, run `uv run fin review list --company <company> --json`.
3. For each pending item, choose an account from `templates/dictionary/schedule_iii.csv`. The account must belong to the item's statement. Write a JSON list to a scratch file with one object per item: `label`, `statement`, `section`, `account`, `confidence` (0 to 1), `reason`.
   - Propose only what the label's wording supports. If you are unsure, use a low confidence (below 0.7 is flagged E302) and say so in `reason`. Never guess to fill a gap.
4. Run `uv run fin map propose <file.json> --company <company>`. Proposals are stored as pending and change no data.
5. Show the user the proposals (label, account, confidence, reason) and tell them to run `/review` or `uv run fin review` to confirm. Do not run `fin review confirm` unless the user has asked you to.
6. Flag failed files and unmapped or unconfirmed labels. Do not state numbers that did not come from `fin` output.
7. For a file that failed a check (E401-E405), read `review/<company>/<file>.error.json`: show each failed check's
   `lines` and the `unmapped` labels. If a line looks mapped to the wrong account (for example old-format "Cash and
   bank balances", which also holds deposits), suggest the `fin map set` command from `to_fix` with the account you
   propose, and why. Run it, then `uv run fin ingest <company> <review copy>`, only when the user asks.
