---
description: Trace a figure to its source cell or page, or show a company's audit history
argument-hint: <company> [metric fiscal-year | --fact <fact_id>]
---

Trace with `fin`: $ARGUMENTS

1. Run `uv run fin audit $ARGUMENTS --json`.
   - `<company> --fact <fact_id>`: one figure, back to its file and `sheet!cell` or `page N`.
   - `<company> <metric> <fiscal-year>`: a metric, back to its facts and each fact's source.
   - `<company>` alone: the company's history (ingests, reviews, queries, writes, reports, failures).
   Get a `fact_id` from `fin query` output (select `f.fact_id`). If a command fails, report its error code and run_id and stop. A metric input with no fact is why the metric is null: report it as missing.
2. Report only what the output says, in this order, for each fact: value and account; raw label and raw text as written; file and sheet!cell or page; the mapping (dictionary row, and each review decision with actor and reason); the ingest run (run_id, status, its events).
3. Flag: `confirmed` false (unconfirmed mapping), a missing archived copy (`archive_exists` false), a superseded document, and any warning or error in the ingest run or history.
4. Describe, don't judge: no recommendations or ratings. Never do arithmetic in prose.
