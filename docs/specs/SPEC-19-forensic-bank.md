# SPEC-19: Bank row and flow checks

## Summary

fin ties each bank statement (opening + credits - debits = closing) and checks consecutive statements for overlaps
and gaps (`diligence/bank.py`, `bank_checks.py`). It does not check each row's balance, and it does not look at the
pattern of money moving in and out. This spec adds both as forensic tests on the bank lines fin already loads (the
owner's plan, X3), into the flags, status and screen of [SPEC-18](SPEC-18-forensic-journal-tests.md). The PDF file
checks of the plan (metadata, fonts, text layer) are skipped (owner, 2026-11-28): a PDF statement is tested on the
rows its parse gave, like an Excel one.

## Scope

1. **Row checks**, per account and statement (single-row tests flag only amounts at or above the query threshold):

   | Test | Flags |
   |---|---|
   | `bank_balance_chain` | each row whose balance is not the previous row's balance + credit - debit; a row with no balance restarts the chain and is counted, not flagged |
   | `bank_date_order` | a row dated before the row above it |
   | `bank_duplicate` | rows with the same date, side, amount, remarks and reference |
   | `bank_round` | months with three or more lines that are multiples of `round_unit` (the lines) |
   | `bank_date_gap` | a stretch of more than `gap_days` with no line inside a statement's period |

2. **Flow checks:**

   | Test | Flags |
   |---|---|
   | `bank_round_trip` | money out and the same amount back (or in, then out) on one account within `flow_days`; the party is named when the remarks of both lines name the same loaded party |
   | `bank_own_transfer` | a line whose remarks name another of the company's accounts (its `ACCT-` token, SPEC-17) with no line of the same amount on that account within 3 days, where that account's statement covers the date |
   | `bank_period_end_credit` | a credit in the last `period_end_days` of a period with a debit of the same amount on the account in the first `reversal_days` of the next |
   | `bank_cash` | cash deposits and withdrawals by month (lines whose remarks hold a word of the user's `cash_words` list), and the lines of a month whose cash total is at or above the threshold |

   `cash_words` is a keyword list the user keeps on Settings (default `CASH DEP`, `CASH WDL`, `ATM`, `SELF`); the
   cash series is shown on the Forensic screen and in `fin forensic flags --test bank_cash`.
3. **Masking.** Account numbers are `ACCT-` tokens and remarks are as filed ([masking.md](../decisions/masking.md));
   a party in a reason is the ledger name or the register token, as the bank reconciliation prints it.
4. **Planted cases** in `mock/generator/data.py` with `mock/EXPECTED.md`: per test a planted row and a near miss;
   own-account transfers need a second synthetic account with its statement. The mock's uncleared cheque (paid
   31-Mar-2025, cleared 03-Apr-2025) is not a reversal and must not flag.
5. **Docs.** `docs/product.md` (the tests in the Forensic section; no new command, so the coverage table is unchanged
   unless one is added), `docs/errors.md` if a code is added.

Out of scope: PDF metadata, fonts and text layer (owner: skipped); image forensics; bank templates; matching bank
lines to the books (built: `bank_vs_tb`, `bank_vs_invoices`).

## Acceptance criteria

- [ ] On the mock set, each test flags exactly the rows `mock/EXPECTED.md` lists for it, and no near miss.
- [ ] A statement with a broken row names that row (file and sheet!row, or page) and the balance it expected.
- [ ] A statement with no row balances reports `bank_balance_chain` as cannot run for it, with the reason.
- [ ] Two statements of one account loaded in either order give the same flags (load-twice test).
- [ ] The tuning log (SPEC-18) holds each bank test's thresholds and counts.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add the bank row and flow checks to the forensic tests | `src/fin/forensic/`, `mock/generator`, `mock/EXPECTED.md`, docs | all criteria |

Risk: not high (no schema file, no new masking: it reads tokens SPEC-17 made). Depends on SPEC-18.
