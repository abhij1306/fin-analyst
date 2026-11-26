# D3 bank statements: product decisions

Only decisions that affect the analysis or what the user sees. Plan: docs/diligence.md §7 D3.

## 1. A statement that does not tie is kept as filed, with a finding
- **Decision:** the plan's "a finding, not a load" is read as: the rows are stored, an open `bank_tie` finding is
  recorded, the outcome says "DOES NOT TIE", and G1 stays open until the user accepts the finding. D5 matching should
  use only statements that tie or whose finding is accepted.
- **Why:** same as the trial balance group-sum finding (rows kept, user decides); refusing would leave nothing to inspect.
- **How to change it:** to refuse the load instead, raise in `bank.py: ingest_bank` when `not st.ties`.

## 2. What "ties" means
- **Decision:** opening + credits − debits = closing, in the account's currency. Opening and closing come from the
  header lines when printed, else from the first row's balance (less its movement) and the last row's balance
  (`opening_from`/`closing_from` record which). Per-row running balances are not checked individually.
- **Why:** works for statements and parses with no summary; still catches a dropped or misread row.
- **How to change it:** `bank.py: read_statement, Statement.ties`.

## 3. Overlap and balance gap findings
- **Decision:** per account (bank, account number, currency) in date order: a statement starting on or before the latest
  earlier end is `bank_overlap`; otherwise an opening that differs from the previous closing is `bank_balance_gap`
  (difference = opening − previous closing). Findings sit on the later statement and clear when the missing statement
  is loaded. They do not block G1 (they go to queries); only `bank_tie` does, as the plan lists.
- **How to change it:** `bank_checks.py: consistency_findings`; to make them block G1, fail the line in `checklist`.

## 4. Period of a statement
- **Decision:** it belongs to the one engagement period (or the stub, then unaudited) holding all its dates; a statement
  crossing a year end is refused (E808). Bank files never supersede each other; the same bytes are E105.
- **How to change it:** `bank.py: bank_period`.

## 5. PDF statements
- **Decision:** the parser chosen in config reads every page once; the parse is saved and re-used (no second upload
  or parse). The engagement and duplicate checks run before any page is sent, so a refused file is never uploaded.
  LlamaParse without `LLAMA_CLOUD_API_KEY`, or a failed call, raises E206 and loads nothing; no parser chosen is E816.
- **Why:** owner's rules (no re-parse, nothing leaves the machine unless chosen, failures stop the load).
- **How to change it:** `bank_pdf.py: read_pdf`.

## 6. Names and account numbers

- **Superseded 2026-11-26** by [masking.md](masking.md): remarks and reference are stored as filed.
- **Decision:** remarks and reference have known masked names replaced by tokens; no new tokens are made from bank
  text. The account number is shown as filed (not on the plan's masked list; needed to chain statements).
- **How to change it:** `bank.py: _insert` (mask `account` there if wanted).

## 7. Currency
- **Decision:** one profile per bank serves INR and USD accounts; amounts stay in the account's currency (no FX; D5).
  The profiles are reconstructions of the layouts in the plan: the diligence user should try them on real files (E807
  lists the header lines when one does not match).
- **How to change it:** `templates/profiles/bank_*.toml`.
