---
description: Answer a question about a company's financials with SQL, sources and an evidence page
argument-hint: <company> <question>
---

Answer this question with `fin`: $ARGUMENTS

1. Write one SELECT on the company database (`companies/<company>.duckdb`). Tables: `documents` (use
   `status = 'loaded'`), `raw_values`, `facts` (value in rupees, `confirmed`); views `current_facts` (one value per
   account, period and scope, already de-duplicated across documents) and `metrics`. Rules:
   - Select `f.fact_id` (or `current_facts.fact_id`) for every fact you use, so each row gets its source.
   - Compute ratios, sums and growth in the SQL. Never do arithmetic yourself.
   - Never mix `standalone` and `consolidated`. If a period appears in two documents (comparatives), pick one
     with `d.file_name` and say which, or use `current_facts`.
   - Every fact has a `period`: `FY25` is the year ended 31 March 2025; `FY26-Q1` to `FY26-Q4` are quarters (Q1
     ends in June), `FY26-H1` / `FY26-H2` half years, `FY26-9M` nine months; `period_months` is its length.
     `fiscal_year` is the fiscal year the period falls in, so filter on `period` (or `period_months = 12` for
     years), never on `fiscal_year` alone: that mixes quarters with the year. A half-year balance sheet is the
     `FY26-H1` period. For quarter-on-quarter or year-on-year questions, compare like periods (`FY26-Q2` with
     `FY26-Q1` or `FY25-Q2`); the `metrics` view has `revenue_growth` (year on year) and `revenue_growth_qoq`.
2. Run it:
   `uv run fin query <company> --question "<question>" --sql "<sql>"`
   On an error, report the code (E502, E504, ...) and the run_id, and stop. Don't work around it.
3. Run `uv run fin evidence <query_id>` and give the path of the evidence page.
4. Reply in exactly this format, using only numbers from the `fin` output:

   **Answer:** one or two sentences.

   **Calculation:** the formula in words, for example "(lt_borrowings + st_borrowings) / total_equity".

   **Source:**

   | Account | Period | Scope | Value (₹) | File | Sheet!cell or page |
   |---|---|---|---|---|---|
   | one row per fact, from `source_file` and `source_ref` |

   **SQL:** the statement, in a `sql` code block.

   **Evidence:** the evidence page path and query_id.

   **Flags:** every row with `source_confirmed` false (unconfirmed mapping), every row with
   `source_read_by` = `claude` and `source_verified` false (read by Claude from a page fin could not read, not yet
   confirmed by an independent reading), missing data, an E604 on the evidence page, or "none". Also flag
   accepted findings that touch the figures used: run `uv run fin findings list <company> --json` and name each
   accepted finding with the same file, period and scope as a row (or the same label), with its code and both
   values, neutrally (the figure is used as filed). If the company has held documents, say their figures are not
   in the answer.

Describe, don't judge: no recommendations or ratings. If the user asks to change data, use
`uv run fin sql <company> --write --sql "<statement>"` (it backs up the database and logs the change), never
`fin query`.
