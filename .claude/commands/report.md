---
description: Write the narrative for a company health report and render it (HTML, Word, PDF)
argument-hint: <company> [--scope standalone|consolidated] [--peers a,b]
---

Produce the health report for: $ARGUMENTS

The report has: a summary of key metrics over the latest five fiscal years (both scopes where loaded); one
section per metric category (profitability, returns, liquidity, solvency, efficiency and working capital,
cash flow, growth, earnings quality, per share, lending: only those with data), each with a trend table and
a chart; the latest quarters with TTM and growth (when quarters are loaded); a peer comparison (with
`--peers`); findings; data coverage; and appendices: condensed standard statements, metric definitions and
the facts behind the metrics. fin builds every table and chart; you write the narrative.

1. Run `uv run fin report <company>` (add `--scope` if given; add `--peers a,b` if the user named other
   companies in the data folder to compare with, and keep passing it on every later run).
   - If it prints `"status": "stub_written"`, it has written `narrative.md` with one `## ` heading per
     section this report has; go to step 2.
   - If it prints `"status": "rendered"`, a narrative already exists and the report is rendered.
     Ask the user whether to rewrite the narrative before changing it.
   - On an error, report the code (E001, E601, E602, ...) and the run_id, and stop.
2. Read the data the report is built from, with `uv run fin sql <company> --sql "..."` only (never open `.duckdb` files):
   - Metrics by category: `SELECT category, metric, period, value, unit FROM metrics WHERE scope = '<scope>' ORDER BY category, metric, period`
   - What each metric means: `SELECT metric, category, definition, formula FROM metric_definitions`
   - Facts: `SELECT account, period, value FROM current_facts WHERE scope = '<scope>' ORDER BY account, period`
   - Coverage: `SELECT * FROM statement_coverage WHERE scope = '<scope>'`, and pages not read:
     `SELECT file_name, unread_pages FROM documents WHERE status = 'loaded' AND unread_pages IS NOT NULL`
   - Unconfirmed facts: `SELECT f.fact_id, f.period, f.value FROM facts f JOIN documents d USING (doc_id) WHERE d.status = 'loaded' AND NOT f.confirmed AND f.scope = '<scope>'`
   - Findings: `uv run fin findings list <company> --json` (accepted ones are in the figures as filed; a held
     document's figures are not in the report)
   - Peers (if any): the same metrics query on each peer, for the latest fiscal year all of them have.
3. Fill in `narrative.md` under each `## ` heading (keep the headings; use `###` below them; leave a heading
   empty rather than delete it):
   - Summary: the main movements across the key metrics, and how the categories relate.
   - Each category: what its table shows over the years.
   - Latest quarters: the latest quarter against the same quarter a year before and the previous quarter, and
     the last-four-quarters (TTM) figures.
   - Peer comparison: what the table shows, in words. Peer figures cannot be referenced, so name no peer
     number; describe the comparison qualitatively and cite this company's own values.
   - Findings: each open or accepted finding neutrally: which document, which line and period, and that the
     figure is used as filed (accepted) or waits for a decision (open); never say which side is right. Mention
     held documents as not included.
   - Data coverage: unmapped figures, pages fin could not read, Claude-read figures not yet verified,
     unconfirmed mappings and missing values (NULL metrics), if there are any.
   - Describe, don't judge: no recommendations, ratings, scores, or buy/sell language.
   - **Never type a number.** Write a reference and the renderer fills in the value from the
     database, in the display unit from config:
     - `{{metric:<metric>:<period>}}`, e.g. `{{metric:ebitda_margin:FY25}}` becomes `18.0%`
     - `{{fact:<account>:<period>}}`, e.g. `{{fact:revenue:FY25}}` becomes `₹134.09 Cr`
     - Periods: fiscal years (`FY25`) and quarters or half years (`FY26-Q1`, `FY26-H1`), e.g.
       `{{metric:ttm_revenue:FY26-Q2}}`.
   - Periods may be written as plain text (`FY25`, `FY26-Q1`). Any other digit (including `1.` list
     markers, years like 2025, or "Q3" on its own) blocks the render with E602. Use `-` bullet lists.
   - Never compute a difference or ratio yourself. To describe a change, cite both values.
4. Run `uv run fin report <company>` again (with the same `--scope` and `--peers`). It lints the narrative,
   renders HTML, Word and PDF, and prints the output paths and the data hash.
   - E602: turn every listed number into a reference (or reword it), then run it again.
   - E601: report the message and run_id; the last good report is kept. "the narrative has text under
     'Peer comparison'" means `--peers` was left out: add it, or empty that section.
5. Reply with the output paths, the data hash, any `missing_values` (references shown as n/a), and
   the data quality flags (findings, unmapped share, unread pages, unverified figures).
