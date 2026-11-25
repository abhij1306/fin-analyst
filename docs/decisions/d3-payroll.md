# D3 payroll: decisions

Product-level decisions of workstream d3-payroll (2026-11-25): what affects the analysis or what the user sees.

## 1. A snapshot belongs to the engagement period that contains its date

- **Decision:** the payroll file's period is the fiscal year (in the engagement) that contains the snapshot date from its header ("Employee Master as on 31-Mar-2025"), or the stub if the date is inside the stub's dates (labelled unaudited). Any other date is E808. A later file for the same period supersedes the earlier one.
- **Why:** the plan says "snapshot date" and "no month column" but not how a snapshot maps to a period.
- **How to change:** `payroll.snapshot_period` in `fin/diligence/payroll.py`.

## 2. Employee name and code are always masked

- **Decision:** both are tokens (`EMP-0001`, `ECODE-0001`) in every table and output; designation, department, vertical and sub-vertical are stored as filed.
- **Why:** the plan masks name and code; designation and department are not names.
- **How to change:** `payroll.rows` (mask another column there).

## 3. Blank amounts are NULL; a row fin cannot identify is refused

- **Decision:** a blank gross salary or CTC is NULL, never 0. A row with no employee name or code, or an unreadable joining or resign date, is E821 and nothing loads. A blank resign date means still employed. Rows named Total are skipped.
- **Why:** no silent defaults: a 0 would look like an unpaid employee and pull medians down.
- **How to change:** `payroll.rows`; to tolerate bad rows, record them as findings instead of raising.

## 4. "Looks monthly": median gross salary or CTC under Rs 1,50,000

- **Decision:** fin warns (load output, `confirm-unit`, G1 line) when the median positive gross salary or CTC is under Rs 1,50,000. It is a warning only; the user decides.
- **Why:** the plan says "warns when the amounts look monthly" without a rule; a median resists a few outliers.
- **How to change:** `MONTHLY_BELOW` in `fin/diligence/payroll.py`.

## 5. The unit is the user's answer; "monthly" blocks G1 and fin never converts

- **Decision:** `fin payroll confirm-unit <co> <file> --annual|--monthly` records the user's answer per file. G1 gets one payroll line per loaded payroll file (none when no payroll file is loaded): it passes on annual, fails when unconfirmed or monthly. fin does not multiply monthly amounts by 12; the company sends an annual snapshot. A superseding file needs its own confirmation.
- **Why:** converting would change the company's data on fin's initiative, which the diligence rules forbid.
- **How to change:** `payroll.checklist` and `payroll.confirm_unit`.

## 6. No duplicate-employee-code check

- **Decision:** not added; the plan's duplicate checks name invoices and bank balances only.
- **How to change:** record a finding in `sources._load_payroll` with `fin.findings.record`.
