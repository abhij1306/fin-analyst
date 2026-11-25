# Decisions: g1-fix

Follow-up to the PR #71 review.

## G1's "every ledger is in the Ledger Vouchers" reads ledger blocks, not voucher lines

- **Decision:** Every ledger block header in a Ledger Vouchers export counts as that ledger being present, even with no voucher lines in the year.
- **Why:** A ledger with only an opening balance (share capital) has no dated lines but is in the export; counting dated lines blocked G1 on it.
- **How to change it:** `diligence/tb_checks.py: _missing_from_vouchers` (read `ledger_entries` again).
