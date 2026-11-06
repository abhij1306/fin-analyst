---
description: Refresh and open the dashboards (company, compare, data quality)
argument-hint: [--build]
---

Open the dashboards with `fin`: $ARGUMENTS

1. Run `uv run fin dashboard $ARGUMENTS`. It snapshots `shared/analytics.duckdb` (so ingests can keep running), then starts the dev server at http://localhost:3000. With `--build` it writes a static site to `<data>/dashboard/build` instead.
   Run the dev server in the background so this session stays usable, and tell the user the URL.
2. If metrics look stale, run `uv run fin metrics refresh` first.
3. If a command fails, report its error code and run_id (E603: the build failed and the last good build was kept) and stop.
4. The dashboards describe; they don't rank or recommend. Point the user to the Data Quality page for unconfirmed mappings and failed validations.
