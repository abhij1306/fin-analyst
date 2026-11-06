---
description: Answer a question about a company's financials with SQL, sources and an evidence page
argument-hint: <company> <question>
---

Answer this question with `fin`: $ARGUMENTS

1. Write one SELECT on the company database (`companies/<company>.duckdb`). Tables: `documents` (use
   `status = 'loaded'`), `raw_values`, `facts` (value in rupees, `confirmed`). Rules:
   - Select `f.fact_id` for every fact you use, so each row gets its source.
   - Compute ratios, sums and growth in the SQL. Never do arithmetic yourself.
   - Never mix `standalone` and `consolidated`. If a year appears in two documents (comparatives), pick one
     with `d.file_name` and say which.
2. Run it:
   `uv run fin query <company> --question "<question>" --sql "<sql>"`
   On an error, report the code (E502, E504, ...) and the run_id, and stop. Don't work around it.
3. Run `uv run fin evidence <query_id>` and give the path of the evidence page.
4. Reply in exactly this format, using only numbers from the `fin` output:

   **Answer:** one or two sentences.

   **Calculation:** the formula in words, for example "(lt_borrowings + st_borrowings) / total_equity".

   **Source:**

   | Account | Year | Scope | Value (₹) | File | Sheet!cell or page |
   |---|---|---|---|---|---|
   | one row per fact, from `source_file` and `source_ref` |

   **SQL:** the statement, in a `sql` code block.

   **Evidence:** the evidence page path and query_id.

   **Flags:** every row with `source_confirmed` false (unconfirmed mapping), missing data, an
   E604 on the evidence page, or "none".

Describe, don't judge: no recommendations or ratings. If the user asks to change data, use
`uv run fin sql <company> --write --sql "<statement>"` (it backs up the database and asks the user to
confirm), never `fin query`.
