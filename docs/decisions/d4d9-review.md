# Decisions: d4d9-review

Product-level choices made while reviewing D4, D5, D6, D8 and D9 as one whole, written 2026-11-25 by the reviewer-builder. Each is a builder's reading until the owner confirms or changes it. Technical clean-ups of the review (shared helpers, constants, which file holds what) are not recorded here.

## 1. A query's value is in rupees everywhere, a foreign-currency line at its rupee value

- **Decision:** The amount of a query, the count and value per kind of difference (in `fin query raise --items`, the export's summary sheet, issue sheets, email and brief) and the golden checks `query_issue` and `query_difference` all use one figure per line: the line's rupee value (for a foreign-currency bank line, its difference converted at the invoice's rate, or the receipt as booked), without sign. Before, the export and `query_difference` added the difference in the line's own currency (USD) as if it were rupees, so a USD line was counted at about 1/80 of its value and did not agree with the query's amount. A line with no rupee value adds nothing to a kind's value and is shown as "with no rupee value"; the brief and the issue sheet show such a line's difference in its own currency, and the issue sheet gains a "Figures in" column and shows the difference in rupees.
- **Why:** The threshold and the query amount are in rupees (plan §3, "Rs 1 lakh"); the accountant must see the same value in the query, the workbook and the brief.
- **How to change it:** `reconcile/items.py:line_rupees`.

## 2. Gate G4 reopens when an accepted adjustment's effects change

- **Decision:** G4's inputs now include each adjustment's revenue effect, cost effect, category, accounts and amount moved as well as its status and EBITDA effect, so any change to what an adjustment does to revenue, costs or margins reopens it.
- **Why:** Plan §5: a gate reopens when the data under it changes; margins on adjusted revenue depend on the revenue effect, which the hash did not cover.
- **How to change it:** `diligence/gates.py:_adjustments_inputs`.

## 3. Two more outputs get golden checks

- **Decision:** `linked_purchase_cost` (the cost a pass-through normalisation strips) and `customer_received` (a customer's invoices matched to a receipt, the received figure of the collections view) can be checked against a person's own working, like the other money figures.
- **Why:** The client's agent scores fin on real documents; these two figures drive an adjustment and the collections view, and had no check.
- **How to change it:** `evals/golden.py` (`PASSTHROUGH_KINDS`, `BANK_KINDS`), `evals/score.py:_FIN_VALUE`, `evals/scaffold.py`.
