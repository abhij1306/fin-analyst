# Decisions: final-fixes (the verified audit of main at 5ac8b49)

Builder readings, not owner decisions. Owner source for the workstream: the owner's brief of 2026-11-25 (ship the
FIX items of the verified audit plan; client-level choices go to the client list below).

## Names learned from a later file are masked in the free text already stored

- **Decision:** When a load adds names to `mask_values`, the same transaction masks those names in the free text
  already stored: bank remarks and reference, Ledger Vouchers particulars, register text fields (location, order
  type, product, service type) and contract billing terms. Only values that change are updated. The load summary
  says how many earlier values changed ("N earlier free-text values masked with the new names").
- **Why:** A salary remark naming an employee stayed readable if the bank statement loaded before the payroll file.
  The masking rule is "no real name goes to Claude", so the result must not depend on load order.
- **How to change it:** `src/fin/diligence/mask.py: FREE_TEXT_COLUMNS, Masker.remask`; the call sites in
  `diligence/bank.py: ingest_bank` and `diligence/sources.py: ingest_source`.

## An employee code is never searched for in free text

- **Decision:** Free text is searched for known names (customers, vendors, users, employee names, ledgers), not for
  employee codes. Payroll still stores each code as a token (`ECODE-0001`), and a cell that is exactly a known code
  is still looked up with `Masker.known`.
- **Why:** Codes can be short numbers ("101", "7"); searching free text for them rewrote amounts and dates in
  remarks and particulars.
- **How to change it:** `src/fin/diligence/mask.py: Masker.text` (`self.codes`).

## `fin sql` cannot read files, and `--write` cannot change the schema

- **Decision:** `fin sql` (read and write) runs with file access closed: `read_csv`, `read_blob`, `glob`, COPY of
  a file and ATTACH of a file fail with E504, so the original files, logs and exports cannot be read through SQL.
  `fin sql --write` also refuses CREATE, DROP, ALTER, PRAGMA, SET, EXPORT and VACUUM (E504, before the backup).
  Data changes (INSERT, UPDATE, DELETE) work as before.
- **Why:** Names are masked in the tables, but the archived originals hold the real ones; a statement such as
  `read_blob` would put them in the output. One `DROP VIEW` made every later command report a wrong version error.
- **How to change it:** `src/fin/db/conn.py: lock_files` (called in `query/run.py: run_select` and
  `query/write.py: write_sql`); `query/write.py: SCHEMA_CHANGE`.

## A figure of 10^16 rupees or more fails its file

- **Decision:** A document with a figure of 10^16 rupees (after the unit conversion) or more is not loaded: it goes
  to `review/` with E890 naming the sheet!cell or page, and the next file loads. Rows the database refuses for any
  other reason fail the same way (E891).
- **Why:** A figure that large is a wrong unit or a misread cell. It cannot be stored (`facts.value` holds up to
  10^18 with two decimals) and one bad file used to abort the whole batch with a traceback. The plan named 10^16.
- **How to change it:** `src/fin/ingest.py: MAX_RUPEES, check_range`.

## `fin delete` also removes exports, evals and saved parses

- **Decision:** `fin delete` removes (after the backup) the company's `exports/<company>/`, `evals/<company>/` and
  the saved PDF parses of its files (`cache/<engine>/<sha256>*`, found through the sha256 of its documents and source
  files; a parse of a file another company has loaded too stays, because it is keyed by content alone). Company-scope label mappings stay in the shared dictionary; the output says how many.
- **Why:** The exports and evals hold masked figures and queries of the company; a parse is the company's page text
  (a LlamaParse response costs money to make again, so it is in the backup).
- **How to change it:** `src/fin/delete.py: company_paths, _cache_files`.
