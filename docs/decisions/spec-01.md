# SPEC-01: fixes from the first client run

Builder readings (2026-11-28) for [SPEC-01](../specs/SPEC-01-client-run-fixes.md) PR 1. Each is a reading until the
owner confirms or changes it.

## "in INR" with no unit word means rupees

- **Decision:** a unit line that says "in INR" with no unit word after it ("All amounts in INR, unless otherwise
  stated") is read as rupees. A unit word after INR still wins ("in INR lakhs" is lakh). "INR" without "in" before
  it is not a unit line.
- **Why:** the client's own change and the two lines it added to `product.md` say exactly this
  ([test run](../test-run/2026-11-27-client.md), observation 5). Requiring "in" keeps a column heading or a label
  that mentions INR from being read as a unit.
- **How to change it:** `src/fin/extract/units.py`: `_IN_INR`, `unit_in`.

## A figure with no recorded source is named, not a failure

- **Decision:** `fin sql --evidence` writes the page for the rows that have a source, names each figure whose raw
  value has no sheet, cell or page, flags the result rows without a source and lists their numbers (from 1) in
  `rows_without_source`. The run exits 0 and logs a warning per such figure; inside a sum the missing line is
  "no source" in `source_ref`.
- **Why:** the spec asks for evidence for the rows with a source and the names of those without. E604 means an
  archived file is missing, which is a different problem; a figure with no position was never given one, so there
  is nothing the user can restore.
- **How to change it:** `src/fin/query/evidence.py`: `_unsourced_section`, `write_evidence`;
  `src/fin/query/run.py`: `NO_SOURCE`, `query`.

## Listing with no open engagement

- **Decision:** `fin query list` and `fin adjust list` print "<co> has no open engagement: no queries" (or
  adjustments), or `[]` with `--json`, and exit 0. Every other diligence command keeps E803.
- **Why:** the spec (observation 18): nothing to list is not an error.
- **How to change it:** `src/fin/diligence/cli.py`: `query_list`; `src/fin/diligence/adjust_cli.py`:
  `adjust_list`; `src/fin/diligence/engagement.py`: `is_open`.

## A defect is shown as E012 with the run id

- **Decision:** an exception with no error class prints `E012: unexpected <type>: <message>`, the run id and the log
  file with the traceback, and exits 2 (a command error); the web UI shows the same line with the code and run id.
  An interrupt (Ctrl-C) is logged without a code and stops the command as before, and Typer's own exits and usage
  errors are not counted as defects.
- **Why:** the spec (item 5): the user sees the code and run id instead of a traceback.
- **How to change it:** `src/fin/audit/logger.py`: `is_defect`, `Run.unexpected_message`, `execute`;
  `src/fin/ui/actions.py`: `perform`.
