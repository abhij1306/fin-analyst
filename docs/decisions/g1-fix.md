# Decisions: g1-fix

Follow-up to the PR #71 review.

## G1's "every ledger is in the Ledger Vouchers" reads ledger blocks, not voucher lines

- **Decision:** The Ledger Vouchers loader stores every ledger block header it reads in the new table `ledger_blocks` (file_id, sheet, row, ledger), even with no voucher lines. G1's check and `fin map ledgers` presence read it.
- **Why:** A ledger with only an opening balance (share capital) has no dated lines but is in the export; counting dated lines blocked G1 on it.
- **Alternatives considered:** Store a zero-line row in ledger_entries (breaks its meaning of one row per voucher line); drop the check.
- **Where in code:** src/fin/diligence/tally.py: ledger_entries; sources.py: _load_lv; tb_checks.py: _missing_from_vouchers; db/schema/sources.sql
- **How to change it:** Point the queries back at ledger_entries and delete the table.

## A block is any undated row with text and no amount

- **Decision:** Block detection is unchanged (`_block_name`); the blocks it finds are now kept.
- **Why:** Same rule that assigns lines to ledgers, so presence and entries agree.
- **Alternatives considered:** A separate stricter block detector.
- **Where in code:** src/fin/diligence/tally.py: _block_name
- **How to change it:** Change `_block_name` or the profile's block field.

## ledger_blocks is a diligence table

- **Decision:** `fin sql --write` refuses it (E806); no new error code.
- **Why:** Like the other source tables.
- **Alternatives considered:** None.
- **Where in code:** src/fin/query/write.py: DILIGENCE_TABLE; audit/errors.py: DiligenceTableWrite
- **How to change it:** Remove it from the regex.
