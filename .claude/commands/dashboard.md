---
description: Refresh and open the dashboards (company, compare, data quality)
argument-hint: [--build]
---

Open the dashboards with `fin`: $ARGUMENTS

1. Run `uv run fin dashboard $ARGUMENTS`. It snapshots `shared/analytics.duckdb` (so ingests can keep running), then starts the dev server at http://localhost:3000. With `--build` it writes a static site to `<data>/dashboard/build` instead.
   Run the dev server in the background so this session stays usable, and tell the user the URL.
2. If metrics look stale, run `uv run fin metrics refresh` first (every ingest refreshes them too).
3. If a command fails, report its error code and run_id (E603: the build failed and the last good build was kept) and stop.
4. Tell the user what the pages show:
   - **Companies** (home): every company loaded, with links.
   - **Company**: pick a company and scope (standalone and consolidated are never combined). A trend chart per metric category over the fiscal years (profitability margins, returns, liquidity, solvency, efficiency days, cash from operations and free cash flow, growth, earnings quality), an "any metric" picker by category (per share and lender metrics too), quarters with trailing twelve months, the standard statements for a chosen period in the documents' unit, and the company's findings and coverage.
   - **Compare**: pick a scope, a category, then a metric; the metric's definition and formula, and every company side by side per fiscal year.
   - **Data quality**: per company, documents, held documents, findings, unconfirmed and unverified facts; then every open or accepted finding with its sources, coverage (mapped share of each statement's figures), statement pages fin could not read, and Claude-read figures not yet verified.
5. The dashboards describe; they don't rank or recommend. Point the user to the Data quality page for findings, held documents, unread pages and unverified figures, and to `/review`, `fin findings` and `/verify` to settle them.
