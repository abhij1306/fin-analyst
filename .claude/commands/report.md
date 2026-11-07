---
description: Write the narrative for a company health report and render it (HTML, Word, PDF)
argument-hint: <company> [--scope standalone|consolidated]
---

Produce the health report for: $ARGUMENTS

1. Run `uv run fin report <company>` (add `--scope` if given).
   - If it prints `"status": "stub_written"`, it has written `narrative.md`; go to step 2.
   - If it prints `"status": "rendered"`, a narrative already exists and the report is rendered.
     Ask the user whether to rewrite the narrative before changing it.
   - On an error, report the code (E001, E601, E602, ...) and the run_id, and stop.
2. Read the data the report is built from, with `fin query` only (never open `.duckdb` files):
   - `SELECT fiscal_year, metric, value, unit FROM metrics WHERE scope = '<scope>' ORDER BY metric, fiscal_year`
   - `SELECT account, fiscal_year, value FROM current_facts WHERE scope = '<scope>' ORDER BY account, fiscal_year`
   - Unconfirmed facts: `SELECT f.fact_id, f.fiscal_year, f.value FROM facts f JOIN documents d USING (doc_id) WHERE d.status = 'loaded' AND NOT f.confirmed AND f.scope = '<scope>'`
   - Findings: `uv run fin findings list <company> --json` (accepted ones are in the figures as filed; a held
     document's figures are not in the report)
3. Fill in `narrative.md` under each `## ` heading (keep the headings; use `###` below them):
   - Describe what the numbers show, section by section. In Observations, point out trends and how
     the sections relate. In Data quality, mention unconfirmed mappings, findings and missing values
     (NULL metrics) if there are any. Describe each accepted finding neutrally: which document, which
     line and period, and that the figure is used as filed; never say which side is right. Mention held
     documents as not included.
   - Describe, don't judge: no recommendations, ratings, scores, or buy/sell language.
   - **Never type a number.** Write a reference and the renderer fills in the value from the
     database, in the display unit from config:
     - `{{metric:<metric>:<FY>}}`, e.g. `{{metric:ebitda_margin:FY25}}` becomes `18.0%`
     - `{{fact:<account>:<FY>}}`, e.g. `{{fact:revenue:FY25}}` becomes `₹134.09 Cr`
   - Fiscal years may be written as plain text (`FY25`). Any other digit (including `1.` list
     markers, years like 2025, or "Q3") blocks the render with E602. Use `-` bullet lists.
   - Never compute a difference or ratio yourself. To describe a change, cite both values.
4. Run `uv run fin report <company>` again. It lints the narrative, renders HTML, Word and PDF, and
   prints the output paths and the data hash.
   - E602: turn every listed number into a reference (or reword it), then run it again.
   - E601: report the message and run_id; the last good report is kept.
5. Reply with the output paths, the data hash, any `missing_values` (references shown as n/a), and
   the data quality flags.
