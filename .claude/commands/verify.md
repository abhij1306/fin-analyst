---
description: Compare what is loaded for a document with a new Claude reading of its pages
argument-hint: <company> <file> [pages]
---

Compare what is loaded for one document with a new Claude reading of its pages: $ARGUMENTS

Use this only when the user asks for it. It is an on-demand check the user can run on any loaded or held document,
whenever they want and as often as they want; fin's own extraction stays the primary path. Each run is recorded
(`verify_runs`, `verify_lines` in the company database). It does not mark figures as trusted: whether a figure read by
Claude is trusted is the user's decision.

1. Find the original file: the path the user gave, or `review/<company>/<file>` / `inbox/<company>/<file>` in the
   data folder. Agree with the user which pages to read (for example the standalone balance sheet page). If they did
   not say, ask.
2. Read the source FIRST, before looking at any fin output for the document (no `fin sql`, no `fin audit`, no
   `fin findings` figures), so the reading is independent. PDF: use the Read tool with the `pages` parameter to see
   the pages as images (needs poppler; `uv run fin doctor` warns when it is missing). Excel: read the cells.
3. Write the reading at `review/<company>/<file stem>.verify-<n>.csv` in the data folder (never in the repo), in the
   reading format (header `statement,scope,period_end,period_months,unit,section,label,value_as_printed,source`;
   the format is described in `/ingest` under "Claude reading" and in `src/fin/extract/reading.py`):
   - one row per figure, every line of the statement (totals included) and every period column on the page;
   - figures exactly as printed, digit for digit, brackets for negatives, `-` for nil, the unit line as printed;
   - `source` is the page number of the original document, or `Sheet!B7`;
   - never compute, round, convert or infer a figure; leave out a figure you cannot read and say so.
4. Run `uv run fin verify <company> <file> --reading <reading.csv>` (add `--json` for the full list). Lines that do
   not agree are findings, not errors (exit 0); they are kept with the document (`fin findings list <company>`). If the command itself fails
   (for example E205, a reading it cannot use), report the error code and run_id and stop.
5. Report: the counts (agree, differ, missing in fin, missing in reading), the verify run it was recorded as, and
   every line that does not agree, with both values (fin's is the value it uses now, which may have been corrected
   since it was printed), as printed, and both sources. Only state numbers from the `fin` output. A verify finding
   does not hold or release the document: a loaded document stays loaded, a held one stays held.
   - differ: one of the two is wrong. Offer to re-check that line on the page; do not resolve it yourself.
   - missing in fin: fin did not load that line (or the whole statement); a label fin read differently also shows
     here and as missing in reading.
   - missing in reading: you did not copy that line; re-check whether it is on the page.
6. Ask the user: **save as is, or have Claude fix it?**
   - Save as is: `uv run fin findings accept <company> <finding ids>` (ids from `uv run fin findings list
     <company>`). The figures stay as loaded; the findings stay listed, marked accepted.
   - Fix: re-check the line on the page, then `uv run fin sql <company> --write --sql "..."` (backed up and
     audit-logged), or `fin ingest --force` with a corrected reading. Run `fin verify` with the same reading again:
     a line that now agrees is resolved.
   `fin verify` itself never changes a figure.

When the user asks how a document was checked before: `uv run fin sql <company> --sql "SELECT run_id, verified_at,
reading_file, agree, differ, missing_in_fin, missing_in_reading FROM verify_runs WHERE file_name = '<file>' ORDER BY
verified_at"`, and `verify_lines` (same `run_id`) for the lines of one run.
