---
description: Ingest financial files for one company, then propose mappings for unmapped labels
argument-hint: <company> [path]
---

Ingest documents for the company in `$ARGUMENTS`.

1. Run `uv run fin ingest $ARGUMENTS`. Report the per-file outcomes and the summary line (loaded, skipped, failed, pending review, run_id). If a command fails, report its error code and run_id and stop. Do not work around it.
2. If anything is pending review, run `uv run fin review list --company <company> --json`.
3. For each pending item, choose an account from `templates/dictionary/schedule_iii.csv`. The account must belong to the item's statement. Write a JSON list to a scratch file with one object per item: `label`, `statement`, `section`, `account`, `confidence` (0 to 1), `reason`.
   - Propose only what the label's wording supports. If you are unsure, use a low confidence (below 0.7 is flagged E302) and say so in `reason`. Never guess to fill a gap.
   - Only when no existing account fits (check `uv run fin map accounts --statement <statement>`), propose a new account: `account` is a new snake_case name and `new_account` is `{"parent": <subtotal it adds into, same statement>, "sign": 1 or -1, "unit": "rupees" or "rupees_per_share", "description": ...}` (`parent` and `sign` null for a top line or a per-share figure). Say in `reason` why no existing account fits. Nothing is added until the user confirms it in review.
4. Run `uv run fin map propose <file.json> --company <company>`. Proposals are stored as pending and change no data.
5. Show the user the proposals (label, account, confidence, reason) and tell them to run `/review` or `uv run fin review` to confirm. Do not run `fin review confirm` unless the user has asked you to.
6. Flag failed files and unmapped or unconfirmed labels. Do not state numbers that did not come from `fin` output.
7. For a file that failed a check (E401-E405), read `review/<company>/<file>.error.json`: show each failed check's
   `lines` and the `unmapped` labels. If a line looks mapped to the wrong account (for example old-format "Cash and
   bank balances", which also holds deposits), suggest the `fin map set` command from `to_fix` with the account you
   propose, and why. Run it, then `uv run fin ingest <company> <review copy>`, only when the user asks.
8. **Pages fin could not read.** A file can fail extraction (E104 scanned, E201, E202, E203, E204), or load with a
   warning that a balance sheet, profit and loss or cash flow page was skipped (`... page N skipped: E20x ...`), or
   load without one of those statements. Tell the user which file, which statements and which pages (from the
   warnings, or the page numbers you find), and **offer** a Claude reading of those pages. Ask first; never read
   them unasked. If the user agrees, follow "Claude reading" below.

## Claude reading (only when the user asked for it)

fin stays the primary path. A reading fills in only what fin could not read, and nothing from it is trusted until a
second, independent reading agrees and the document's validation gates pass.

1. Read the source FIRST, before looking at any fin output for the document (no `fin query`, no error.json
   figures), so the reading is independent. PDF: use the Read tool with the `pages` parameter on the original file
   (`review/<company>/<file>` after a failure) to see the pages as images. Excel: read the cells.
2. Write the reading file at `review/<company>/<file stem>.reading1.csv` in the data folder (never in the repo).
   UTF-8 CSV with this header, one row per figure (one line in one period column):

   `statement,scope,period_end,period_months,unit,section,label,value_as_printed,source`

   - `statement`: `balance_sheet`, `profit_and_loss` or `cash_flow`. `scope`: `standalone` or `consolidated`, from
     the page title.
   - `period_end`: the column's date as `YYYY-MM-DD`. `period_months`: 3, 6, 9 or 12 from the column header
     ("Quarter ended" 3, "Half year ended" 6, "Year ended" 12); empty for a balance sheet.
   - `unit`: the page's unit line exactly as printed, e.g. `(₹ in Lakhs)`.
   - `section`: the heading the line sits under, as printed (may be empty). `label`: the line's label as printed.
   - `value_as_printed`: the figure exactly as printed, digit for digit, with its brackets for a negative, `-` for
     nil. Quote it when it has commas (`"1,234.56"`).
   - `source`: the page number of the ORIGINAL document (`page 12`), or `Sheet!B7`.
   - Copy every line of the statement, totals and subtotals included, for every period column. Never compute,
     round, convert units or infer a figure. If a figure is unreadable, leave the line out and tell the user;
     do not guess it.
3. Load it: `uv run fin ingest <company> <original file> --reading <reading1.csv>`. fin loads the reading only for the statements it could not read itself, through the same validation
   gates. Report the outcome. If a gate fails (E401-E405), show the error.json lines; do not edit the reading to
   make it pass. Re-read the page and fix only a figure you misread, and say which.
4. Second, independent reading: read the same pages again from scratch, without opening `reading1.csv`, and write
   `review/<company>/<file stem>.reading2.csv` the same way.
5. Run `uv run fin verify <company> <original file> --reading <reading2.csv>`. Report the counts and every line
   that does not agree (a finding, not an error), with both values and their sources. Findings are never resolved
   automatically: show them, re-check the page if the user asks, and change data only when the user asks (`fin sql --write`, or a
   corrected reading and `fin ingest --force`).
6. Say how many facts were read by Claude and how many are verified. Unverified Claude-read facts must be flagged
   in every answer that uses them.
