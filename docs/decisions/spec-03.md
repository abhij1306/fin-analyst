# SPEC-03: Files and ingest in the UI

Builder readings of [../specs/SPEC-03-ingest-in-ui.md](../specs/SPEC-03-ingest-in-ui.md), built in one PR (all four
rows, owner 2026-11-28). The owner confirms or changes them. Format: [README.md](README.md).

## 1. A file's outcome is read from what ingest left, not from the job

- **Decision**: the Files screen builds one row per file from the company database (each document's current
  version), `review/<company>/*.error.json` (each failed file not loaded since) and the inbox files whose content is
  not loaded (waiting). A failed file that later loads, with a reading or a fixed copy, shows as loaded.
- **Why**: the rows then show the same after a reload, a restart or a CLI ingest; no second record of an outcome.
- **How to change it**: `src/fin/ui/file_rows.py: file_rows`.

## 2. "Read by AI, not checked" stays until a trust decision exists

- **Decision**: a document with figures from a reading shows how many, marked "read by AI, not checked". There is no
  action yet that marks them checked, so the mark stays; `fin verify` does not remove it (it is another Claude
  reading, not the user's decision).
- **Why**: the spec says the user decides whether a read figure is trusted, and names no action for it.
- **How to change it**: `src/fin/ui/templates/_file_rows.html` (the badge) and a decision service in
  `src/fin/services/decisions.py`.

## 3. The unit answer loads the held file with SPEC-04's `--unit`

- **Decision**: a file held for its unit (E203, SPEC-04) shows "Which unit is this file in?" with one button per
  answer (lakhs, crores, millions, thousands, rupees). A button starts the job **Load with the unit**
  (`fin.services.ingest.load_with_unit`), which loads the file's `review/` copy as `fin ingest <company> <file> --unit
  <answer>` does: the unit is stored as given by the user. Re-ingest of a document loaded that way passes the same
  unit and who gave it.
- **Why**: SPEC-04 (#124) merged first with the hold and `--unit`, and left the UI form to SPEC-03 (its reading 7).
  An earlier draft of this PR asked Claude to write a reading with the unit; the answer needs no reading.
- **How to change it**: `src/fin/services/next_steps.py` (`NEXT_STEPS["E203"]`), `src/fin/services/ingest.py`
  (`load_with_unit`), `src/fin/ui/job_routes.py` (`CHOICES`, `ON_ITEM`).

## 4. A company has a name and a short code

- **Decision**: **New company** takes the company's name (stored in `company_profile`, shown on its Files screen)
  and a short code, which is what fin already calls the company: its database, folders and the company picker use
  the code.
- **Why**: fin names a company by its folder; the spec's "a name and a short code" read as the display name and that
  folder name.
- **How to change it**: `src/fin/services/files.py: create_company`, `src/fin/db/schema/company.sql`.

## 5. Delete removes every version of the file

- **Decision**: **Delete** on a document removes all its versions (the documents with its sha256), their rows, the
  archived original, notes, a reading no other document uses and the inbox copy with the same content, after a
  backup of the company database and those files to `backups/<run_id>/`. The audit tables keep their rows.
- **Why**: a row on the screen is a file; deleting only the current version would leave the superseded ones, which
  are in no current figures and have no row, and leaving the inbox copy would load it again at the next Ingest.
- **How to change it**: `src/fin/documents.py: delete_document`.

## 6. Re-ingest loads from the archived original

- **Decision**: **Re-ingest** loads the document's archived copy again as a new version (`--force`), with the
  reading it was loaded with, so it works when the inbox copy is gone.
- **Why**: the archive is the copy fin keeps of what was loaded; the inbox is the user's.
- **How to change it**: `src/fin/services/ingest.py: reingest_document`.

## 7. Delete has a CLI command; Re-ingest and New company do not

- **Decision**: Delete is also `fin document delete <company> <ids>` (same service, same run record apart from
  `via`). Re-ingest and New company are service functions only: the CLI already re-ingests with
  `fin ingest <company> <file> --force` and makes a company by ingesting into `inbox/<company>/`.
- **Why**: Delete sits with the decision buttons (`POST /act`), and every UI action there has a CLI command with
  the same run record (SPEC-02, `tests/unit/test_services_parity.py`). The existing commands are unchanged (the CLI
  stays as it is, owner 2026-11-28).
- **How to change it**: `src/fin/cli/documents.py`; a command in `src/fin/cli/` for the other two.
