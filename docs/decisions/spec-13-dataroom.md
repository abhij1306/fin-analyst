# SPEC-13 data-room intake (PR 1 and 2): builder's readings

Readings, not owner decisions, until the owner confirms them.

## Kinds are the loaders' kinds; "reference databook" waits for SPEC-10

- **Decision**: a plan row's kind is a column profile's kind (`trial_balance`, `ledger_vouchers`, `registers`,
  `payroll`, `bank`, `orderbook`, `gst`), `statements`, `archive` or `unknown`. The spec's "reference databook"
  is not a kind yet: it has no profile to find it by and no loader until SPEC-10 PR 1.
- **Why**: the spec says the kind comes only from the file type and the profile headers; a kind fin cannot load
  would sit in the plan with nothing to do.
- **How to change it**: `src/fin/dataroom/kinds.py: PLAN_KINDS`, `load.py: PROPOSABLE` and the CHECKs in
  `db/schema/dataroom.sql`, when SPEC-10 adds its loader.

## A workbook fitting profiles of two kinds is not given either

- **Decision**: when the headers fit profiles of more than one kind, the row is `unknown` with the reason
  `several_kinds`; the assistant proposes one and the user approves it.
- **Why**: picking one would be a guess.
- **How to change it**: `src/fin/dataroom/kinds.py: _excel`.

## Statement sheets fin cannot read are listed, not loaded

- **Decision**: a workbook with a statement sheet that `fin ingest` would fail on (no period header, no unit line,
  an uncached formula) is `statements` with the reason `statements_unreadable` and the next step "have the assistant
  read it".
- **Why**: the plan should show a failure before the load, with its next step.
- **How to change it**: `src/fin/dataroom/kinds.py: _excel`.

## Only .zip is unpacked, three archives deep

- **Decision**: fin unpacks .zip (Python's standard library); .rar, .7z, .tar and .gz are listed with "unpack it into
  the folder yourself". An archive inside an archive is unpacked too, to three levels; deeper ones are listed.
- **Why**: no new dependency; data rooms are mostly zip. The depth limit bounds an archive of archives.
- **How to change it**: `src/fin/dataroom/kinds.py: ARCHIVE_SUFFIXES, OTHER_ARCHIVES`, `scan.py: MAX_DEPTH`.

## The archive limit is required in config.toml

- **Decision**: `[dataroom] archive_limit_mb = 2048` (Q10's default, 2 GB unpacked per archive) is a required table;
  a config.toml without it is E002.
- **Why**: config.toml has no fallback values, and the owner said there is no compatibility code for earlier data
  (owner, 2026-11-28): re-create the data folder.
- **How to change it**: `src/fin/config.py: DEFAULT_CONFIG_TOML, _archive_limit`.

## A refused archive is refused whole

- **Decision**: one unsafe entry (outside the folder, a link, a duplicate path), a password or the size limit refuses
  the whole archive; nothing of it is unpacked.
- **Why**: criterion 2; a partly unpacked archive would give a plan with files silently missing.
- **How to change it**: `src/fin/dataroom/unpack.py: _check, _extract_all`.

## Approving a row is how the user confirms a proposed kind

- **Decision**: the assistant proposes a kind with `fin dataroom propose` (with a reason); proposing clears the row's
  approval, and the user's `fin dataroom approve` both confirms the kind and chooses the row for the load. A file fin
  cannot use cannot be approved without a proposed kind.
- **Why**: the spec has the assistant propose and the user confirm; one user step instead of two.
- **How to change it**: `src/fin/dataroom/load.py: propose, approve`.

## The load carries on past a failed file

- **Decision**: `fin dataroom load` loads each approved row in its own transaction; a file that fails (no open
  engagement, no profile match, a period outside the engagement) is recorded on its row with the error and the next
  file loads; the command exits 1. A failed row stays approved and is tried again on the next load. A file whose
  content is already loaded (by `fin ingest` or an earlier load) is marked loaded.
- **Why**: "each document in its own transaction as today" and "one outcome per file"; `fin ingest` of a folder
  behaves the same way.
- **How to change it**: `src/fin/dataroom/load.py: load, _load_one`.

## Load order

- **Decision**: statements, then trial balances, then Ledger Vouchers, registers, payroll, bank, orderbook, GST; plan
  order within a kind.
- **Why**: Ledger Vouchers are checked against their period's trial balance (E811), so it must load first.
- **How to change it**: `src/fin/dataroom/load.py: LOAD_ORDER`.

## Hidden files and links

- **Decision**: hidden files and folders (a name starting with ".") are left out, as `fin ingest` does; a link is
  listed with "copy the file itself into the folder", never followed.
- **Why**: rule 8 of docs/engineering.md (a path from input stays inside its root).
- **How to change it**: `src/fin/dataroom/scan.py: _walk`.
