---
description: Show what is loaded per company and what is waiting for the user
argument-hint: [company]
---

Show the status of the companies in fin: $ARGUMENTS

1. Run `uv run fin status $ARGUMENTS` (no argument: every company). If it fails, report the error code and run_id
   and stop.
2. Report it per company, in plain words:
   - periods loaded (years, halves, quarters, nine months) and any gaps (a period missing between loaded ones);
   - held documents and their open findings: not in figures or metrics until the user decides;
   - findings open and accepted;
   - mappings waiting for review;
   - figures read by Claude that are not yet verified;
   - files in the inbox not yet ingested.
3. Then offer the next step for each item that is waiting, and run it only when the user says so:
   - inbox files not ingested: `/ingest <company>`;
   - held documents or open findings: show them with `uv run fin findings list <company>` and ask the user to save
     as is or have Claude fix them (step 7 of `/ingest`);
   - mappings waiting for review: `/review`;
   - unverified figures read by Claude: `/verify <company> <file>`;
   - a gap: say which period's document seems to be missing; never fill a gap with assumptions.
