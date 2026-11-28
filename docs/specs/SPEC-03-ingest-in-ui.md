# SPEC-03: Files and ingest in the UI

## Summary

Today the Files screen only puts uploads in `inbox/<company>/`; ingest runs in chat or a terminal, and the reasons a
file failed are terminal output. In the client run all 12 files failed and the user could see none of that without
Claude ([obs. 4, 6](../test-run/2026-11-27-client.md)). After this spec a user adds files, starts ingest and sees
one row per file with its outcome, the reason in plain words and the next step.

## Scope

- **Start ingest** from the Files screen for a company (a job, SPEC-02).
- **One row per file:** loaded (figures, unmapped labels, how many were read by AI), held, failed. A failed or held
  row shows the error code, the message and one next step:
  - no unit line: "Which unit is this file in?" with the choices (SPEC-04);
  - no tables found or no text layer: "Read with the assistant" or "Choose a PDF add-on";
  - unsupported type: what to do.
- **Read with the assistant.** Starts a chat turn that produces the reading file and runs
  `fin ingest --reading`. The user decides whether a read figure is trusted; the row says "read by AI, not checked"
  until then.
- **Company.** Create a company from the screen (a name and a short code).
- **Re-ingest and delete** a document, each behind a confirmation that says what will change.

Out of scope: new extraction code (SPEC-04), mapping (SPEC-05), drag-and-drop of whole folders.

## Acceptance criteria

- [ ] From an empty data folder, with no terminal: create a company, upload the synthetic clean fixtures, ingest,
      and see every file loaded.
- [ ] With the synthetic "problem" fixtures, each failure row shows its code, message and next step; a test checks
      every ingest error code has a next-step text.
- [ ] Ingest progress shows the file being read; the page stays usable.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Run ingest from the Files screen with one outcome row per file | `src/fin/ui`, `src/fin/services/ingest.py` | criteria 1, 3 |
| 2 | Show a next step for every ingest failure | error-to-next-step table, templates | criterion 2 |
| 3 | Add company creation, re-ingest and delete to the Files screen | UI, services | tests green |
| 4 | Start an assistant reading from a failed file | UI chat hand-off | a held scan loads through the screen |
