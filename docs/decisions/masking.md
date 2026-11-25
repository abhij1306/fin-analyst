# Decisions: masking scope

**NEEDS CLIENT CONFIRMATION.** The client's agent asks the client about this first (docs/client-handoff.md, step 5).
The sentences below that start "Reading" are ours, not the client's words.

What the client said (answers of 2026-11-24): anonymise payroll before any third-party processing (mask name and
code at minimum), and mask the identifier fields they starred: sales Customer Name, handler and submitted-by;
purchase Vendor Name.

## Masking scope

- **Decision:** Names are stored once in `mask_values` and every table, `fin` output and log carries a token
  (`CUST-0001`, `VEND-0001`, `USER-0001`, `EMP-0001`, `ECODE-0001`, `LEDG-0001`). What is masked:
  - sales register: customer, handler, submitted-by; purchase register: vendor (the starred fields);
  - payroll: employee name and code (designation, department and the like stay as filed);
  - Tally party ledgers (under Sundry Debtors and Sundry Creditors), and Ledger Vouchers particulars;
  - bank remarks and reference: known masked names inside them are replaced; no new tokens are made from bank text;
  - free-text register columns (location, order type, product, service type): known names inside them are replaced.
  Real names come back only in `fin query export`, for the accountant.
- **Why (our readings):**
  (a) Claude counts as third-party processing, because Claude Code sends what `fin` prints to Anthropic. So masked
  fields are tokens in everything `fin` prints, not only before an outside tool sees them.
  (b) Tally party ledgers and bank remarks hold the same customer and vendor names, so they are masked too.
  (c) The accountant gets real names (the export is theirs to read); nothing else does.
  (d) LlamaParse, the optional cloud PDF add-on, sees pages unmasked. The owner allowed it with a warning in the
  README; Docling and LiteParse run locally.
- **Options for the client:**
  1. **Keep as is.**
  2. **Mask only the starred fields and payroll.** Stop masking Tally party ledgers, Ledger Vouchers particulars and
     bank remarks. Change: `templates/profiles/tally_trial_balance.toml` (`[mask] groups`), `tally_ledger_vouchers.toml`
     and `bank_*.toml` (`[mask] fields`); free-text columns in `FREE_TEXT` of `src/fin/diligence/registers.py`.
     Names would then be the same value in the registers (token) and the books (name), so matching by party would
     need `diligence/tally.py: stored_names` revisited.
  3. **Mask nothing for Claude; only before other third parties.** `fin` would print real names. Change:
     `src/fin/diligence/mask.py: Masker.token` returns the real value; the E810 refusal in
     `src/fin/query/run.py: refuse_masked` goes. A large change in what Claude sees; the client's rule (a)
     would have to be accepted as not applying.
  4. **Mask more** (locations, product names, account numbers). Registers: `MASKED` and `FREE_TEXT` in
     `src/fin/diligence/registers.py`; payroll: `payroll.rows` in `src/fin/diligence/payroll.py`; bank account number:
     `bank.py: _insert`; a new kind needs a prefix in `mask.py: PREFIX`.
- **How to change it:** the files named in each option. Existing data must be loaded again (no migrations).
