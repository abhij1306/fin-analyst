# SPEC-13: Data-room intake

## Summary

The client's request ([test-run/2026-11-27-client.md](../test-run/2026-11-27-client.md), "What the client asked
for"): read a whole data room in one go instead of loading files one by one. The loaders exist
(`fin ingest --kind`, `templates/profiles/*.toml`); what is missing is finding the files, saying what each one is,
and loading what the user approves in one run. It comes first of the three client specs: nothing else runs without
the sources.

## Scope

1. **`fin dataroom scan <co> <folder>`** walks the folder and writes a plan; it loads nothing.
   - Archives are unpacked, each into its own new directory under the data folder. An entry whose path leaves that
     directory, a link, or an archive over the size limit in `config.toml` is refused and listed.
   - Files with the same content hash are listed once, with the other paths as duplicates.
   - Each file gets a kind: statements, trial balance, ledger export, register, bank, payroll, GST return, reference
     databook (SPEC-10), or unknown. The kind comes from the file type and from which column profile's headers the
     file has, which is the test `--kind` already uses. fin does not guess from file names or contents beyond that.
     For an unknown file the assistant may propose a kind; the user confirms it.
   - A file fin cannot use is listed with the reason and one next step (ask the company for the Excel export, or an
     assistant reading). A ledger that arrives as PDF is this case: no new parsing code (`CLAUDE.md`).
2. **The plan is shown before anything loads**: file, kind, profile, period where the file states it, duplicates,
   and what will be skipped. `fin dataroom load <co>` loads the rows the user approved, each document in its own
   transaction as today, all under one run id, and prints one outcome per file.
3. **Re-running** a scan on the same folder adds only new files; a loaded file is not loaded twice.
4. **Files screen** (SPEC-03): choose a folder or upload an archive, see the plan as a table, tick and load (a job).
5. **Gates:** loading new sources reopens G1 and G2, as today.

Out of scope: reading layouts the loaders cannot read; classifying by file name; fetching from cloud drives.

## Open issues

- **Register headings.** The register profile's column headings are synthetic. The real export's headings must be
  mapped once ("Add a column profile", [development.md](../development.md)). Needs the client's headings, not their
  data (human task H4).
- Q10 in [README](README.md): the archive size limit.

## Acceptance criteria

- [ ] A synthetic data room (nested folders, one archive, two duplicate files, one PDF ledger, one unknown file)
      scans to a checked-in expected plan.
- [ ] An archive with an entry that points outside its directory is refused and nothing is written outside it.
- [ ] Loading the approved plan produces one run id; a second scan and load changes nothing.
- [ ] Every unusable file in the plan has a reason and a next step (a test over the reasons).

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Scan a data room into a plan: unpack, drop duplicates, name each file's kind | `src/fin/dataroom`, CLI, schema, docs | criteria 1, 2, 4 |
| 2 | Load an approved data-room plan in one run | `src/fin/dataroom`, `ingest.py` callers | criterion 3 |
| 3 | Show and approve the data-room plan on the Files screen | `src/fin/ui` | UI test |

Risk: PR 1 unpacks archives from outside (paths, size). A separate reviewer.
