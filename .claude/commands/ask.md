---
description: Answer a question about a company's financials with SQL, sources and an evidence page
argument-hint: <company> <question>
---

Answer this question with `fin`: $ARGUMENTS

1. Write one SELECT on the company database (`companies/<company>.duckdb`). Tables: `documents` (use
   `status = 'loaded'`), `raw_values`, `facts` (value in rupees, `confirmed`); views `current_facts` (one value per
   account, period and scope, already de-duplicated across documents), `metrics` (one row per period, scope and
   metric, with `unit` and `category`), `metric_definitions` (what each metric means and its formula) and
   `standard_statements` (the P&L, balance sheet and cash flow in standard order, with `explained`, `coverage` and
   an `Unmapped / other` line where a printed subtotal is more than its mapped lines). Rules:
   - For a ratio, margin, return or growth, use the `metrics` view; don't recompute it. Find the metric with
     `SELECT metric, category, definition, formula FROM metric_definitions`. Categories: `profitability` (gross,
     EBITDA, EBIT, PBT, PAT margins), `returns` (ROE, ROCE, ROA on closing balances; `*_avg` on average balances),
     `liquidity` (current, quick, cash ratio), `solvency` (debt/equity, net debt, net debt/EBITDA, interest
     cover, debt/assets), `efficiency` (asset turnovers, receivable/inventory/payable days, cash conversion
     cycle), `cash_flow` (CFO, free cash flow, CFO/EBITDA, CFO/PAT, capex ratios), `growth` (year on year, and
     3- and 5-year CAGR), `earnings_quality` (other income and exceptional items over PBT, effective tax rate),
     `per_share` (EPS as reported), `lender` (NBFCs and banks: net interest income, cost to income, credit cost,
     assets/equity), `ttm` (last four quarters), `headline` (a quarter's revenue and PAT).
   - A metric whose inputs are missing is NULL or absent: say which input is missing (`metric_inputs` rows with
     no `fact_id`); never fill it in.
   - Select `metric_inputs.fact_id` with a metric (`JOIN metric_inputs USING (period, scope, metric)`), so each
     input gets its source.
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
   `uv run fin sql <company> --question "<question>" --sql "<sql>" --evidence`
   It prints the rows, the query_id and `evidence_path` (a page showing the source of every fact).
   On an error, report the code (E502, E504, ...) and the run_id, and stop. Don't work around it.
3. Reply in exactly this format, using only numbers from the `fin` output:

   **Answer:** one or two sentences.

   **Calculation:** the formula in words, for example "(lt_borrowings + st_borrowings) / total_equity".

   **Source:**

   | Account | Period | Scope | Value (₹) | File | Sheet!cell or page |
   |---|---|---|---|---|---|
   | one row per fact, from `source_file` and `source_ref` |

   **SQL:** the statement, in a `sql` code block.

   **Evidence:** the evidence page path and query_id.

   **Flags:** every row with `source_confirmed` false (unconfirmed mapping), every row with
   `source_read_by` = `claude` or `docling` and `source_verified` false (read by Claude or by the Docling PDF
   add-on from a page fin could not read, not yet confirmed by an independent reading), missing data, an E604 on the evidence page, or "none". Also flag
   accepted findings that touch the figures used: run `uv run fin findings list <company> --json` and name each
   accepted finding with the same file, period and scope as a row (or the same label), with its code and both
   values, neutrally (the figure is used as filed). If the company has held documents, say their figures are not
   in the answer.

Describe, don't judge: no recommendations or ratings. If the user asks to change data, add `--write`:
`uv run fin sql <company> --write --sql "<statement>"` (it backs up the database and logs the change).
