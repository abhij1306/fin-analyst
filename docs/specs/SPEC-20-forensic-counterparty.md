# SPEC-20: Counterparty and scheme tests, GSTR-2B and earnings-quality measures

## Summary

The implementation guide to the forensic standard on fraud risk (FAIS 120) names indicators that map to fin's registers, ledgers and bank lines: new
vendors with high volume, year-end sales followed by credit notes, parties settled by journal, loans to parties with
no trading, expenses capitalised. This spec adds them as forensic tests (the owner's plan, X4) into the flags of
[SPEC-18](SPEC-18-forensic-journal-tests.md), with two measures of earnings quality, and the GSTR-2B intake with a
purchase register against GSTR-2B reconciliation (owner, 2026-11-28: GSTR-2B is built, not backlog).

## Scope

1. **Tests** (single-entry tests flag only amounts at or above the query threshold; parties are register tokens,
   joined to ledgers through `mask_values` as the reconciliations do):

   | Test | Flags |
   |---|---|
   | `cp_new_vendor` | a vendor first seen in the period (no invoice in any earlier loaded period) whose purchases are at least `new_vendor_share` of the period's |
   | `cp_vendor_spike` | a vendor's month at `spike_factor` times its median month, with `spike_history` months of history |
   | `cp_under_limit` | purchase register invoices within `limit_band` below an approval limit the user states |
   | `cp_credit_note` | a sales invoice in the last `period_end_days` of a year followed by a credit note voucher (`credit_note_types`) on the party's ledger in the first 60 days of the next; a year whose next period's Ledger Vouchers are not loaded cannot run, and says so |
   | `cp_setoff` | a rebuilt journal voucher debiting one party ledger and crediting another (receivables and payables) |
   | `cp_loan_no_trade` | a ledger mapped to loans (`loans_current`, `loans_non_current`, `loans`) whose party has no sales or purchase in any loaded period |
   | `cp_capitalised` | a rebuilt voucher debiting a fixed-asset account (under `fixed_assets` in the dictionary) whose counter-ledger is neither cash and bank nor a party |

   Dropped: "payroll names or vendors sharing a bank reference" (the payroll file has no bank column and a bank
   reference is a cheque number: [forensic.md](../decisions/forensic.md), question 12).
2. **Measures.** Metrics `accruals` (PAT - CFO) and `accruals_to_assets` (PAT - CFO over total assets), annual, in
   `company_metric_definitions.sql`, no value when an input is missing. A view `period_end_revenue`: per engagement
   period, sales register revenue in the last month and in the last `period_end_days`, and its share of the period's
   revenue, shown on the Forensic screen and in `fin analytics`.
3. **GSTR-2B intake.** A profile `templates/profiles/gstr2b.toml` for the B2B and B2B-CDNR sheets of the portal's
   Excel download, from the published layout ([forensic.md](../decisions/forensic.md), "The GSTR-2B layout", sources
   cited there). Loaded with `fin ingest <co> <file> --kind gst` like GSTR-1 and 3B; the supplier's trade name is
   masked as kind `vendor` on load. A synthetic GSTR-2B file in the mock set, built to that layout.
4. **Reconciliation `purchase_register_vs_gstr2b`.** By vendor and invoice number, per month of the period; then, for
   lines still unmatched, by invoice number and taxable value alone, shown as matched with "vendor names differ" and
   both tokens, so a supplier whose 2B name is not the register's name is not reported missing on both sides. An invoice
   in the register and not in 2B (credit the vendor has not reported), in 2B and not in the register, or with a
   different taxable value; at or above the threshold it needs a query (`fin query raise --items`). It runs with
   `fin reconcile`, is listed in the discrepancies view at the transaction level, and like the four GST
   reconciliations is not a gate input.
5. **Planted cases** in `mock/generator/data.py` with `mock/EXPECTED.md` (per test a planted row and a near miss,
   the GSTR-2B file with one invoice the vendor did not report and one with a different value); Settings gains
   `credit_note_types` and the approval limits SPEC-18 added.
6. **Docs.** `docs/product.md` (tests, measures, the 2B profile and reconciliation, the command coverage table if a
   command is added), `docs/errors.md`, `docs/eval-kinds.md` (a golden kind for the 2B reconciliation).

Out of scope: GSTR-2A; vendor GST filing status from the portal (public records, X5, backlog); peer comparison.

## Acceptance criteria

- [ ] On the mock set, each test flags exactly the rows `mock/EXPECTED.md` lists for it, and no near miss.
- [ ] The 2B reconciliation lists exactly the planted invoices with the right side and difference (SQL test).
- [ ] `accruals` equals PAT - CFO for each mock year and has no value for the stub (no CFO).
- [ ] No 2B supplier name reaches a table other than `mask_values` or any output; the masking registry test covers it.
- [ ] A test whose input is missing (no purchase register, fewer than `spike_history` months) reports cannot run or
      too few entries with what it needs.

## PR plan

| # | Title | Scope | Done when |
|---|---|---|---|
| 1 | Add the counterparty tests, the accrual and period-end measures and the GSTR-2B reconciliation | `src/fin/forensic/`, `reconcile/gst.py`, `templates/profiles/gstr2b.toml`, schema files, `mock/generator`, `mock/EXPECTED.md`, docs | all criteria |

Risk: high (schema files, a new masked field). Depends on SPEC-18. Human task H8 checks the 2B headings.
