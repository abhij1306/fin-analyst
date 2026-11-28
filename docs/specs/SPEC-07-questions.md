# SPEC-07: Questions: schema guide and saved metrics

## Summary

7 of the 25 queries in the client run failed on a column name the assistant guessed, and the first question needed a
ratio fin does not have, which was then lost ([obs. 12, 14](../test-run/2026-11-27-client.md)). This spec gives the
assistant the schema from `fin` itself and lets a user keep a metric.

## Scope

1. **`fin schema [<co>] [--table name] [--json]`.** The views meant for questions (`current_facts`,
   `standard_statements`, `metrics`, `metric_inputs`, `metric_definitions`, the source views), each with its
   columns, types, one line of meaning and one example query. Generated from the database and from comments in the
   schema files, so it cannot drift.
2. **A better E504.** When a query names a missing column, the message lists the columns of the tables in the query
   and points to `fin schema`.
3. **Source columns in one place.** A view that joins a fact to its file, sheet and cell or page, confirmation and
   reader, so a question needs one join, with the names the failed queries reached for.
4. **Saved metrics.** `fin metric add <name> --formula ... --unit ratio|rupees|days --definition "..."` stores a
   company-level or shared metric built from accounts and existing metrics. It is computed with the built-in ones,
   appears in `metric_definitions` marked "user", and traces to its inputs. `fin metric list|remove`.
5. **`/ask`** (the skill) reads `fin schema` first and offers to save a ratio it had to write by hand.

Out of scope: natural-language-to-SQL inside `fin`; a query builder screen.

## Acceptance criteria

- [ ] A test fails when a view's column is missing from `fin schema` output.
- [ ] The seven failed queries from the client run, rewritten against the synthetic company, either run or fail with
      a message that names the right column.
- [ ] A saved metric appears in the `metrics` view, the Excel export and the dashboard source.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add fin schema and name the available columns in E504 | `src/fin/cli`, `src/fin/db`, schema comments | criteria 1, 2 |
| 2 | Add a fact sources view | `company_views.sql`, docs | view test |
| 3 | Add saved metrics | `src/fin/metrics`, schema, CLI, docs | criterion 3 |
