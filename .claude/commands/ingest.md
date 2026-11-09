---
description: Ingest financial files for one company, then propose mappings for unmapped labels
argument-hint: <company> [path]
---

Ingest documents for the company in `$ARGUMENTS`.

1. Run `uv run fin ingest $ARGUMENTS`. Report the per-file outcomes and the summary line (loaded, skipped, failed,
   pending review, held, run_id). If a command fails, report its error code and run_id and stop. Do not work around
   it.
2. If anything is pending review, run `uv run fin review list <company> --json`.
3. For each pending item, choose an account from `templates/dictionary/schedule_iii.csv`. The account must belong to the item's statement. Write a JSON list to a scratch file with one object per item: `label`, `statement`, `section`, `account`, `confidence` (0 to 1), `reason`.
   - Propose only what the label's wording supports. If you are unsure, use a low confidence (below 0.7 is flagged E302) and say so in `reason`. Never guess to fill a gap.
   - Only when no existing account fits (check `uv run fin map accounts --statement <statement>`), propose a new account: `account` is a new snake_case name and `new_account` is `{"parent": <subtotal it adds into, same statement>, "sign": 1 or -1, "unit": "rupees" or "rupees_per_share", "description": ...}` (`parent` and `sign` null for a top line or a per-share figure). Say in `reason` why no existing account fits. Nothing is added until the user confirms it in review.
4. Run `uv run fin map propose <company> <file.json>`. Proposals are stored as pending and change no data.
5. Show the user the proposals (label, account, confidence, reason) and tell them to run `/review` to confirm. Do not run `fin review confirm` unless the user has asked you to.
6. Flag failed files and unmapped or unconfirmed labels. Do not state numbers that did not come from `fin` output.
7. **Findings.** A file reported as `held as doc_id N ... K findings` loaded, but something in it does not add up
   (E401 balance sheet, E402 subtotal, E403 cash flow, E404 a figure differs from another document, E405 a unit
   factor). This is a finding about what the company filed, not an error. A held document is not in current figures
   or metrics.
   1. Run `uv run fin findings list <company>`. Show the user each open finding: the finding id, the code, the
      period, this document's figure and where it is, and what it was compared with and where. Describe it
      neutrally; do not say which side is right.
   2. Ask the user, for each held document: **save as is, or have Claude fix it?**
   3. Save as is: run `uv run fin findings accept <company> <finding ids>`. The document is loaded exactly as filed;
      the findings stay attached, marked accepted.
   4. Fix: first look at the source page or cells and tell the user what you found. Then use one of these, as the
      user agrees:
      - a label mapped to the wrong account (for example old-format "Cash and bank balances", which also holds
        deposits: map it to `cash_and_bank_balances`): `uv run fin map set <company> --statement <statement>
        --label "<label>" --account <account>`, then `uv run fin ingest <company> <file> --force`;
      - an unmapped label that is part of the total: confirm its mapping with `/review`;
      - a figure fin read wrongly: `uv run fin sql <company> --write --sql "UPDATE facts SET value = ... WHERE
        fact_id = ..."` (backed up and audit-logged).
      Every held document is checked again after `fin sql --write` and `fin review confirm`, and `fin ingest
      --force` checks the new version. Report the result: a finding that no longer appears is resolved; a document
      with no open findings is loaded. Never change data to make a check pass when the source itself shows the
      figure: that is the user's call to save as is.
8. **Pages fin could not read.** A file can fail extraction (E104 scanned, E201, E202, E203, E204), or load with a
   list of statement pages it did not read (`not read: <statement> page N: E20x ...` under the file's outcome; for
   a failed file also `unread_pages` in its error.json), or load without one of those statements. Tell the user
   which file, which statements and which pages (from those lines, or the page numbers you find), and **offer** a
   Claude reading of those pages. Ask first; never read
   them unasked. If the user agrees, follow "Claude reading" below. A failed file is not loaded; a file that loaded
   with unread pages is, and its reading is loaded with `--force`.
   With the optional Docling add-on on (`[pdf] docling = true`), fin has already tried Docling on those pages: the
   outcome says how many figures Docling read (`read by Docling`) and the pages still listed are the
   ones neither read. Without it, the run log says the pages could also be read by installing the add-on
   (`./install.sh --docling`); mention that option, do not install it unasked.

## Claude reading (only when the user asked for it)

fin stays the primary path. A reading fills in only what fin could not read, through the same validation gates.
Claude does not verify its own reading: whether a Claude-read figure is trusted is the user's decision.

1. Read the source FIRST, before looking at any fin output for the document (no `fin sql`, no `fin findings`
   figures), so the reading is independent. PDF: use the Read tool with the `pages` parameter on the original file
   (`review/<company>/<file>` after a failure) to see the pages as images (this needs poppler, installed by
   `./install.sh`; `uv run fin doctor` warns when it is missing). Excel: read the cells.
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
3. Load it: `uv run fin ingest <company> <original file> --reading <reading1.csv>`, adding `--force` when the file
   already loaded (a partial load, or a file with pages listed as not read): fin refuses a file it has already
   ingested (E105, "skipped") before it looks at the reading, and `--force` loads it again as a new version. A file
   that failed outright was never loaded, so it needs no `--force`. fin loads the reading only for the statements it
   could not read itself, through the same validation gates. Report the outcome. If the document is held with findings, follow step 7; do not edit the reading to
   make a check pass. Re-read the page and fix only a figure you misread, and say which.
4. Say how many facts were read by Claude (or Docling). They feed metrics like any other figure; answers show them
   as read by Claude or Docling (information, not a flag). If the user wants to check the document against a new
   Claude reading, that is `/verify <company> <file>`, any time and as often as they like; do not start it unasked.
