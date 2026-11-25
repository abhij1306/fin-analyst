# Decisions: D3 review (registers, bank, payroll, eval kit)

Product-level decisions of the whole-feature review of D3 (#72–#76), 2026-11-25. Builder readings, not owner decisions.
The review found no error in what the loaders load, mask or flag; what it added is the golden checks below.

## 1. Register totals are scored per sheet, over every row that was loaded

- **Decision:** a `register_total` golden line is the sum of Amount in Rs (before GST) over every invoice row of the
  sales or the purchase sheet of the loaded registers file, in rupees. Duplicate invoice rows and rows dated outside
  the engagement count, because fin loads and keeps them. Its period is the file's period (`FY25`, `FY24-FY25`).
- **Why:** the registers are loaded as filed (a duplicate is a finding, not a drop), so the person's sum of the column
  is what fin should show. A total that left out duplicates would hide a loading error.
- **How to change it:** `evals/score.py: _FIN_VALUE` (the `register_total` query); `docs/client-handoff.md` step 4.

## 2. Payroll totals are annual rupees as filed; the user's unit answer is not applied

- **Decision:** a `payroll_total` line is the sum of gross salary or of CTC over the employees of the loaded payroll
  file, as stored (read as annual). A blank amount adds nothing. A file the user confirmed as monthly is still compared
  with what the file says, since fin never converts.
- **Why:** the check is about reading the file correctly; the unit is the user's separate answer and gate G1's.
- **How to change it:** `evals/score.py: _FIN_VALUE` (the `payroll_total` query).

## 3. Only the closing balance of a bank statement is scored

- **Decision:** `bank_closing` stays the only bank golden kind. Opening balance, debit and credit totals are not
  scored.
- **Why:** a wrong opening, row or total breaks the tie (`bank_tie`) or the balance join (`bank_balance_gap`), which
  fin already reports as findings; the closing balance anchors the statement to the printed one.
- **How to change it:** add a kind as in `docs/development.md`, "Eval checks".

## 4. A bundle shows what a register or payroll total is, never a name

- **Decision:** a failing `register_total` or `payroll_total` line shows `sales`, `purchase`, `gross_salary` or `ctc`
  as its ledger (not `ledger-1`); the counts add register lines by kind and payroll files by confirmed unit
  (annual, monthly, not confirmed). No file name or source text.
- **Why:** these words are fixed labels, not names, and the maintainers need to know which total failed.
- **How to change it:** `evals/bundle.py: _failures`; `evals/counts.py: counts`.
