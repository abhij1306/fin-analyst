# SPEC-17: decisions from the client's answers

Builder's readings ([README](README.md)); the owner confirms or changes them.

## 1. Bank account numbers (PR 1)

### Which numbers are masked
- **Decision:** the account number a statement's header gives (the profile's `[header] account` pattern) is stored
  once in `mask_values` (kind `bank_account`) and replaced by `ACCT-0001` in `bank_statements`, `bank_lines`,
  findings, output and logs. The same number, found as a whole number inside any bank line's remarks or reference
  (a transfer between the company's own accounts), is replaced too, also in lines loaded before that account's
  statement was. Other digits in bank text (cheque numbers, a third party's account) stay as filed.
- **Why:** the client asked for "bank account numbers too" (answer 1). fin knows an account number only when a
  statement names it; guessing which other digit runs are account numbers would be a parsing heuristic.
- **How to change it:** `bank_mask.py: mask_text, remask_lines`; `bank.py: mask_statement`.

### An account number is its own kind
- **Decision:** a number masked as `bank_account` never shares a token with a name or code of another kind with the same
  text (a payroll code with the same digits keeps its `ECODE-` token, and the account gets `ACCT-`).
- **Why:** a customer who is also a vendor is one party, so the name kinds share tokens; an account number and an
  employee code with the same digits are not one thing.
- **How to change it:** `mask.py: Masker.token`.

### The saved parse of a PDF
- **Decision:** `cache/bank/<sha256>.json` is written with tokens. The numbers are found with the bank profiles' account
  patterns and made tokens in their own short transaction just before the parse is saved, so a load that fails later
  (E808, for example) still leaves a saved parse fin can read again without calling the parser. Such a token has no
  `first_file_id` (NULL): it was made from a saved parse, before any file loaded.
- **Why:** the spec asks that the saved parse hold the token; parsing again after every failed load would cost a
  LlamaParse call each time.
- **How to change it:** `bank_pdf.py: read_parse`, `bank_mask.py: mask_parse, unmask_parse`.

### What still shows the number
- **Decision:** the original file (archive, inbox), a PDF evidence crop, and a file name that contains the number show it
  as filed; `fin query export` writes the real number for the accountant, like the other masked fields.
- **Why:** the spec states this limit (item 1); the export follows "like the fields masked today".
- **How to change it:** `export_data.py: Unmasker` (to keep the token in the export).

### Earlier data
- **Decision:** no migration and no `--force` path for bank files. Statements loaded before hold the number; the docs say
  to re-ingest (`fin delete <co>`, then load again, which also removes their saved parses).
- **Why:** the owner, 2026-11-28: "this is a greenfield build. No migrations, no compatibility code, no fallbacks or
  special cases for earlier users or earlier data". This replaces the spec's "loaded again with `--force`".
- **How to change it:** `cli/ingest.py: _check_usage` and `loading.py: refuse_duplicate`, if a reload is wanted.
