# D3 payroll: decisions

Workstream d3-payroll (2026-11-25). Scope: DILIGENCE_PLAN.md §7 D3 "Payroll" and `payroll_lines` (§4). Each entry
says what was decided, why, what else was considered, where it is in the code and how to change it. Entries 1-2 are
readings of the plan where it was ambiguous.

## 1. The snapshot date gives the period; payroll files have a single date, not a range

- **Decision:** a payroll file's period is the engagement period containing its snapshot date: the fiscal year if in
  `engagement.periods` (basis `audited`), else the stub if the date is within the stub's dates (basis `unaudited`),
  else E808. A later file for the same period supersedes the earlier one (the existing rule for source files).
- **Why:** the plan says "snapshot date" and "no month column" but not how a snapshot becomes a period. The other
  source files must be one full year or exactly the stub; a snapshot is a point in time, so "contained in" is the
  natural test, and the existing `source_files.period` / supersede logic then works unchanged.
- **Alternatives:** require the snapshot to be a fiscal year end (31 March) (too strict: a snapshot taken in
  June for FY26 would be refused); make `period` NULL for payroll (breaks `NOT NULL` and supersede).
- **Where:** `fin/diligence/payroll.py:snapshot_period`; called from `fin/diligence/sources.py:ingest_source`.
- **How to change:** edit `snapshot_period`. `basis` follows the period (stub = unaudited).

## 2. Where the snapshot date is read: a header line, via a profile group `date`

- **Decision:** the profile's `period` regular expression for a payroll profile has one named group, `date` (not
  `start`/`end`), matching a title line above the header ("Employee Master as on 31-Mar-2025"). `Table.start` and
  `Table.end` are both that date. `snapshot_date` on each row is that date.
- **Why:** reuses the profile-driven reader (`fin/extract/ledger.py`) and its E808 when no line gives the date,
  with no payroll special case in the reader beyond one branch in `_period`.
- **Alternatives:** a snapshot-date column (the plan lists the column in the table but the Sparsh layout is
  unknown, and a per-row date could disagree between rows); the file name (fragile).
- **Where:** `fin/extract/ledger.py:_period`, `fin/extract/profiles.py:_parse` (group check by kind),
  `templates/profiles/sparsh_payroll.toml`.
- **How to change:** change the `period` pattern in the profile; for a date column, add it to the profile's
  fields and read it in `payroll.rows`.

## 3. One profile, `sparsh_payroll`, with invented headers

- **Decision:** the profile reads one header row: Employee Code, Employee Name, Designation, Department, Vertical,
  Sub Vertical, Date of Joining, Date of Resignation, Gross Salary, CTC. Required fields (`FIELDS["payroll"]`):
  employee name, employee code, gross salary, CTC. The profile's other columns are optional fields of the layout
  but, as for every profile, all of a profile's `[columns]` must be found for it to match.
- **Why:** the real Sparsh export was not available; the headers follow the plan's column list. A different
  layout is E807 (it lists the header lines), and the answer is a new profile, as for Tally.
- **Alternatives:** guess several header spellings (speculative, and the project rule is no layout heuristics).
- **Where:** `templates/profiles/sparsh_payroll.toml`, `fin/extract/profiles.py:FIELDS`.
- **How to change:** edit the `[columns]` table, or copy the profile (docs/CUSTOMISING.md).

## 4. Masking: employee name and code are always tokens, fixed in code

- **Decision:** `employee_name` becomes `EMP-0001`, `employee_code` `ECODE-0001` through the existing `Masker`
  (kinds already in `mask.PREFIX`), whatever the profile says. Other columns (designation, department,
  vertical, sub-vertical) are stored as filed. The same name in another file gets the same token.
- **Why:** the plan says both are masked; there is no case for an unmasked employee name, so it is not a profile
  option. Designation and department are not names.
- **Alternatives:** profile `[mask] fields` for payroll (an option that could be forgotten and leak a name).
- **Where:** `fin/diligence/payroll.py:rows`.
- **How to change:** mask another column by passing its text through `masker.text` or `masker.token` in `rows`.

## 5. Blank amounts are NULL; a row without name or code is E821

- **Decision:** a blank gross salary or CTC is stored NULL (never 0); text that is not a number is E204 (existing
  `parse_number`). A row with other cells but no employee name or code, and a joining or resign date that is not
  a date in the profile's `date_format`, are E821 and nothing loads. A blank, "-", "NA" or "nil" resign date is
  NULL (still employed). Rows whose name or code is in the profile's `skip` ("Total", "Grand Total") are skipped.
- **Why:** "no silent defaults". A 0 would pull medians down and look like an unpaid employee. A row fin cannot
  identify can be neither masked nor traced. Not dropping an unparseable date follows the Ledger Vouchers rule
  (E204 for an undated row).
- **Alternatives:** make a bad row a finding and load the rest (the data is the company's, but a row with no key
  cannot be stored in a table whose key is lineage, and a bad date is fin's reading problem, so refusing is
  safer); default blanks to 0 (a silent default).
- **Where:** `fin/diligence/payroll.py:rows`, `_money`, `_day`; `PayrollRowInvalid` (E821).
- **How to change:** to tolerate a bad row, record it as a finding in `sources._load_payroll` instead of raising.

## 6. "Looks monthly": median gross or CTC under Rs 1,50,000

- **Decision:** a loaded file looks monthly when the median of its positive gross salaries, or of its positive
  CTCs, is under `MONTHLY_BELOW` = Rs 1,50,000 (rupees, after the profile's `unit`), computed in SQL with
  `median ... FILTER (WHERE x > 0)`. fin warns in three places: the load output (a `WARNING:` line), `confirm-unit`
  (a note, also when the user said annual: the user's answer is recorded as given) and G1's payroll line.
- **Why:** the plan says "warns when the amounts look monthly" with no rule. A median is robust to a few
  outliers (directors, interns). Annual pay of a company's staff is rarely under Rs 1.5 lakh at the median, while
  monthly pay rarely reaches it. It is a warning, not a gate: the user decides.
- **Alternatives:** a ratio to the statements' employee cost (needs a mapped salary account; that is D4's
  `payroll_vs_gl`); a config.toml setting (a constant is enough until a company needs another number).
- **Where:** `fin/diligence/payroll.py:MONTHLY_BELOW`, `looks_monthly`.
- **How to change:** change the constant; to make it configurable add a key to `config.toml` and read it in `looks_monthly`.

## 7. The unit confirmation: table `payroll_units`, `--monthly` keeps G1 failing

- **Decision:** `fin payroll confirm-unit <co> <file> --annual|--monthly` (exactly one flag; `<file>` is the file
  name or file_id of a loaded payroll file of the open engagement) writes one row to `payroll_units` (file_id,
  unit, run_id, time), replacing an earlier answer; every answer is an audit-log event (`payroll` stage), with the
  database backed up first. Without an answer the file's G1 line fails; `annual` passes; `monthly` fails ("load an
  annual snapshot"). fin never multiplies the amounts by 12.
- **Why:** the plan says the unit is "confirmed by the user" and G1 needs "payroll unit confirmed". If the user says
  monthly, converting would change the company's data on fin's own initiative, which the diligence rules forbid;
  asking the company for an annual file (or a profile that reads it) is the route. A superseding file needs its
  own confirmation because its figures are new.
- **Alternatives:** a column on `source_files` (that table is shared with the sibling workstreams and the unit
  only applies to payroll); a `diligence_events` row (would need a new `kind` in the shared audit schema; the
  events table already records the answer); a `--monthly` that scales by 12 (see above).
- **Where:** `fin/diligence/payroll.py:confirm_unit`, `fin/cli/payroll.py`, table `payroll_units` in
  `db/schema/sources.sql`. E820 (`PayrollFileNotFound`) for no loaded payroll file named, a superseded one, or a
  name matching several loaded files (use the id).
- **How to change:** to convert on `--monthly`, multiply in `payroll.rows` using the confirmed unit and require
  confirmation before load; to log in `diligence_events`, add `'payroll_unit'` to its `kind` check and call `run.record`.

## 8. G1: one payroll line per loaded payroll file, none otherwise

- **Decision:** `payroll.checklist` adds to G1 a line per loaded payroll file: "payroll <file> (<period>): unit
  confirmed annual by the user" (pass) or the failing forms. An engagement without a payroll file gets no payroll
  line at all (the plan says "only when a payroll file is loaded"). G1's inputs gain `payroll units` (file_id, unit)
  so a confirmation or a new answer reopens it; a new payroll file is already in the `source files` input.
  `tb_checks._files` now selects only trial balance and Ledger Vouchers files, so a payroll file alone does not
  trigger the trial balance checks ("no loaded trial balance for FY25").
- **Why:** keeps D3's gate contribution in its own module (`gates.py` only calls it), so sibling workstreams
  adding bank and register lines merge cleanly.
- **Alternatives:** put payroll in `tb_checks.py` (its name and module contract are about the trial balance).
- **Where:** `fin/diligence/payroll.py:checklist, inputs`; `fin/diligence/gates.py:_data_checklist, _data_inputs`;
  `fin/diligence/tb_checks.py:_files`.
- **How to change:** edit `payroll.checklist`.

## 9. Not done (out of scope or not asked)

- No duplicate-employee-code check: the plan's duplicate and overlap checks name invoices and bank balances only.
  A finding is easy to add in `sources._load_payroll` with `fin.findings.record`, as the trial balance does.
- No `payroll_vs_gl` (D4), monthly salary workbooks, registers or bank (siblings).
- Errors: E820 and E821 only (README "Error codes").
