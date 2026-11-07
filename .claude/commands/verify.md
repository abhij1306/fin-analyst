---
description: Check loaded figures against an independent Claude reading of the source pages
argument-hint: <company> <file> [pages]
---

Verify what is loaded for one document against a Claude reading of its pages: $ARGUMENTS

Use this only when the user asks for it. It is an on-demand check; fin's own extraction stays the primary path.

1. Find the original file: the path the user gave, or `review/<company>/<file>` / `inbox/<company>/<file>` in the
   data folder. Agree with the user which pages to read (for example the standalone balance sheet page). If they did
   not say, ask.
2. Read the source FIRST, before looking at any fin output for the document (no `fin query`, no `fin audit`, no
   error.json figures), so the reading is independent. PDF: use the Read tool with the `pages` parameter to see the
   pages as images. Excel: read the cells.
3. Write the reading at `review/<company>/<file stem>.verify-<n>.csv` in the data folder (never in the repo), in the
   reading format (header `statement,scope,period_end,period_months,unit,section,label,value_as_printed,source`;
   the format is described in `/ingest` under "Claude reading" and in `src/fin/extract/reading.py`):
   - one row per figure, every line of the statement (totals included) and every period column on the page;
   - figures exactly as printed, digit for digit, brackets for negatives, `-` for nil, the unit line as printed;
   - `source` is the page number of the original document, or `Sheet!B7`;
   - never compute, round, convert or infer a figure; leave out a figure you cannot read and say so.
4. Run `uv run fin verify <company> <file> --reading <reading.csv>` (add `--json` for the full list). Lines that do
   not agree are findings, not errors (exit 0); they are recorded in the audit log. If the command itself fails
   (for example E205, a reading it cannot use), report the error code and run_id and stop.
5. Report: the counts (agree, differ, missing in fin, missing in reading), how many facts were marked verified, and
   every line that does not agree, with both values as printed and both sources. Only state numbers from the `fin`
   output.
   - differ: one of the two is wrong. Offer to re-check that line on the page; do not resolve it yourself.
   - missing in fin: fin did not load that line (or the whole statement); a label fin read differently also shows
     here and as missing in reading.
   - missing in reading: you did not copy that line; re-check whether it is on the page.
6. `fin verify` never changes a figure. Change data only when the user asks: `uv run fin sql <company> --write
   --sql "..."` (backed up and audit-logged), or `fin ingest --force` with a corrected reading.
