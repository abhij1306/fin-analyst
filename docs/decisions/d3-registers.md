# Decisions: d3-registers

Product-level choices for registers (docs/diligence.md §7 D3), written 2026-11-25 by the builder. Each is a builder's reading until the owner confirms or changes it.

## 1. A register's period comes from its invoice dates

- **Decision:** A registers file has no period header. Its period is the fiscal year(s) of its first and last invoice date (`FY25`, `FY24-FY25`); basis is `unaudited` if any invoice is in the stub, else `audited`. Lines dated outside the engagement are loaded and counted in the output, not refused.
- **Why:** A billing export covers whatever span the system holds; hiding or refusing out-of-scope lines would be worse than counting them (the date lets later reconciliations filter).
- **How to change it:** To refuse instead, raise E808 where `outside` is counted in `diligence/registers.py:load`.

## 2. A later registers file replaces the earlier one, whatever its period

- **Decision:** The earlier file and its open or accepted findings become superseded.
- **Why:** Two loaded registers files would double count in every comparison.
- **How to change it:** `diligence/sources.py:_supersede` (the registers condition in the WHERE).

## 3. Masked fields, and names inside other columns

- **Decision:** Customer, vendor, handler and submitted-by are tokens (`CUST-`, `VEND-`, `USER-`), the same name the same token as in the Tally files. Known names inside location, order type, product and service type are replaced too. Ids, invoice numbers, purchase ids and currency stay as filed.
- **Why:** The plan fixes which register columns are masked; a name in a free-text column is still a name.
- **How to change it:** `MASKED` and `FREE_TEXT` in `diligence/registers.py`.

## 4. What counts as a duplicate invoice, and its finding

- **Decision:** Sales: the same invoice number twice (case and spacing ignored). Purchases: the same vendor and invoice number twice (different vendors may reuse a number). Each repeat is a finding against the first row; both rows are kept. The finding's figure and difference are the repeated line's Amount in Rs, so a query raised from it carries the amount at stake.
- **Why:** Plan: the same invoice number twice is a finding, then a query. First minus repeat would be 0 for a true duplicate.
- **How to change it:** `diligence/registers.py:duplicate_findings` (the key and the amounts).

## 5. A row that is not an invoice line stops the load (E813)

- **Decision:** A row with no invoice number, invoice date, party or Amount in Rs, or a date not in the profile's format, loads nothing and names sheet!row. Total rows are skipped through the profile.
- **Why:** No silent defaults: a blank Amount in Rs read as 0 would vanish from a reconciliation.
- **How to change it:** `diligence/registers.py:_line`; skip names in the profile's `[rows] skip`.

## 6. Amount in Rs is stored as filed, before GST

- **Decision:** It is not recomputed from Amount x Currency Rate, and there are no GST columns.
- **Why:** Plan §9 item 5; the USD comparison with the books is D4's.
- **How to change it:** `diligence/registers.py:_line`.

## 7. Registers need no trial balance, and G1 is unchanged

- **Decision:** Registers load with an open engagement only. Open duplicate-invoice findings do not block G1.
- **Why:** The plan's D3 additions to G1 are payroll unit and bank ties; register findings go through the existing query flow.
- **How to change it:** Add a line for open `register_duplicate_invoice` findings in `diligence/tb_checks.py:checklist`.
