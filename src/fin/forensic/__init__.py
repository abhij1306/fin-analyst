"""The forensic layer (SPEC-18; docs/decisions/forensic.md): tests that look for entries made to
mislead, on documents fin already loads.

- `registry`: a test (`ForensicTest`), what it returns (`Outcome`, `Flag`) and the registered tests,
  discovered from the modules of `fin.forensic.families` (one module per family: a new family is a
  new module there, with no edit to shared code).
- `settings`: `config.toml [forensic]` (thresholds with their defaults and reasons, the user's
  inputs), per company in `[forensic.companies.<name>]`; `fin forensic set` writes it.
- `books`: the Ledger Vouchers lines of the engagement's periods and the vouchers rebuilt from them.
- `run`: runs tests and keeps every run and its flags; `status` lists each test's state;
  `handoff`: `fin query raise --flags`.

A flag is the company's to explain: it becomes a query or an FDD finding the user approves; nothing
is changed to clear it. No test scores or rates an entry.
"""
