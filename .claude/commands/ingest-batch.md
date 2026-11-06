---
description: Ingest every company folder under a folder (default inbox/), then propose mappings
argument-hint: [folder]
---

Ingest all companies from the folder in `$ARGUMENTS` (default: the `inbox/` folder of the data folder, `~/FinAnalysis/inbox` unless `FIN_DATA_DIR` is set).

1. Run `uv run fin ingest-batch <folder>`. Report the per-file outcomes and the summary line (loaded, skipped, failed, pending review, run_id). If the command fails, report its error code and run_id and stop. It is safe to re-run after an interruption.
2. Run `uv run fin review list --json`, which lists the pending items of every company.
3. For each company with pending items, follow steps 3 and 4 of `/ingest`: choose accounts from `templates/dictionary/schedule_iii.csv`, write one proposals file per company, and run `uv run fin map propose <file.json> --company <company>`.
4. Summarise per company: loaded, failed (with codes), proposals stored. Tell the user to run `/review` to confirm. Do not run `fin review confirm` unless the user has asked you to.
