# SPEC-07: questions, schema guide and saved metrics

Builder readings (2026-11-28) for [SPEC-07](../specs/SPEC-07-questions.md), all three PR-plan rows in one PR. Each is
a reading until the owner confirms or changes it.

## Which views `fin schema` lists

- **Decision:** `current_facts`, `fact_sources`, `standard_statements`, `metrics`, `metric_inputs`,
  `metric_definitions` and `document_facts`. "The source views" in the spec is read as `fact_sources` (scope item 3);
  the diligence views are not listed. Each view's meaning is one line for the view, not one per column.
- **Why:** the failed queries were all about facts, metrics and their sources (observation 12). Listing every
  diligence view would bury the ones a question needs.
- **How to change it:** `src/fin/query/guide.py`: `QUESTION_VIEWS`, plus the comment lines above the view.

## fact_sources keeps fin's column names

- **Decision:** the view has `source_file`, `source_ref`, `source_confirmed`, `source_read_by` and `label` (the names
  the failed queries used), but keeps `doc_id` and `account` rather than adding `document_id` or `input_account`. A
  query using those names gets an E504 that lists `doc_id` and `account`.
- **Why:** `doc_id` and `account` are the names in every other table and view; two names for one column would make
  joins ambiguous. Criterion 2 allows "fail with a message that names the right column".
- **How to change it:** `src/fin/db/schema/company_views.sql`: `fact_sources`.

## Saved metrics: where, how computed, which periods

- **Decision:** `fin metric add` without `--company` saves the metric for every company (`shared/dictionary.duckdb`,
  copied into each company database and into one created later); with `--company` for that company only. fin
  rewrites the view `saved_metrics_wide` from the saved rows, so the values are computed in SQL, live, with the
  built-in metrics. A formula is computed in DOUBLE and rounded once (rupees to 2 places, ratio and days to 6). A
  name that is both a metric and an account (revenue, pat, cfo) means the metric. A saved metric is annual when it
  uses an annual-only metric, else for every period. Category is `user`.
- **Why:** the spec asks for a company-level or shared metric computed with the built-in ones and traced to its
  inputs. A rewritten view stays current with the data, where stored values would go stale after a re-ingest or a
  review. Exact paise arithmetic (`div6`) only fits one division; DOUBLE rounded once is what the CAGRs already do.
- **How to change it:** `src/fin/metrics/saved.py` (`compile_formula`, `view_sql`, `_apply`);
  `src/fin/db/schema/company_saved_metrics.sql`.

## Saved metrics change only through fin metric

- **Decision:** `fin sql --write` refuses statements naming `saved_metrics` or `saved_metric_operands` (E504).
- **Why:** a row written there directly would not be in the view that computes it (engineering rule 10: refuse in
  the function).
- **How to change it:** `src/fin/query/write.py`: `SAVED_METRIC_TABLE`.
