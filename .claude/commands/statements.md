---
description: Show a company's standard statements, or export them with the metrics to Excel
argument-hint: <company> [period] [standalone|consolidated] [export]
---

Show the standard statements for: $ARGUMENTS

1. If the user asked for Excel or "all years", run `uv run fin export <company>` and give the path it prints
   (`reports/<company>/<company>_financials.xlsx`: one sheet per statement and scope with every period side by
   side, and a Metrics sheet). Stop there.
2. Otherwise run `uv run fin statements <company>`, adding `--period <FY25 or FY26-Q1 ...>` and
   `--scope <standalone|consolidated>` when the user named them (default: the latest fiscal year, both scopes).
   If it fails, report the error code and run_id and stop.
3. Show the output as it is (a code block). The lines are the dictionary's standard accounts in standard order,
   amounts in rupees, whatever labels the filing used. Then, in plain words and using only figures from the
   output:
   - each `Unmapped / other` line: a printed subtotal its mapped lines do not fully explain (the line shows how
     much they explain). It usually means a line is waiting for review (`/review <company>`) or was not read;
   - that figures from unconfirmed mappings and held documents are not included (`/status <company>`).

Describe, don't judge: no recommendations or ratings.
