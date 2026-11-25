# Decisions: d4-passthrough

Product-level choices for pass-through and adjustment effects (docs/diligence.md §7 D4), written 2026-11-25 by the builder. Each is a builder's reading until the owner confirms or changes it.

## 1. Pass-through revenue and growth come from the sales register

- **Decision:** `revenue_split` is built from the sales register's Amount in Rs (before GST), by the period of the invoice date: core revenue (customers not on the list), pass-through revenue (customers on the list) and their sum. Core revenue growth is core against the previous annual period in scope, and is blank for the stub, for the first period in scope and when the earlier core revenue is not positive. The statements' revenue is not split: it stays in `adjusted_ebitda`.
- **Why:** Pass-through is identified by customer, and only the register has the customer. Growth must leave pass-through out (§3), and a first period has no earlier period to compare with inside the engagement.
- **How to change it:** `db/schema/diligence_split_views.sql`, view `revenue_split` (the `sales` and final `SELECT`).

## 2. The "linked purchase cost" is the purchase register lines of the pass-through lines' Purchase Ids

- **Decision:** `linked_purchase_cost` sums the purchase register lines whose Purchase Id appears on a pass-through sales line of the period, each Purchase Id once, whatever the purchase's own date. A pass-through line with a Purchase Id is shown as licence (software), one without as services; a services line has no cost in this figure. The link is shown as information: `passthrough_lines` lists each sale with its purchase (or NULLs when no purchase has that Purchase Id).
- **Why:** The diligence user said a Purchase Id links a sale to the OEM purchase (populated: software or licence; blank: services). The normalisation adjustment needs a cost to strip; this is the only cost fin can trace to the sale. Claude proposes the adjustment from these figures and the user accepts or rejects it.
- **How to change it:** `diligence_split_views.sql`, the `linked` part of `revenue_split`.

## 3. The list is the user's, replaced as a whole, and checked against the register

- **Decision:** `fin engagement passthrough` takes customer tokens (not names) and where the list came from. Setting it replaces the earlier list for the engagement. A token that is on no loaded sales register line is refused (E845), and so is a list when no sales register is loaded. Without a list, all revenue is core.
- **Why:** The plan says the user supplies the list per engagement. A token that matches nothing would silently leave revenue in core.
- **How to change it:** `diligence/passthrough.py`: `set_customers`, `_check_customers`.

## 4. How an adjustment's effects are read

- **Decision:** The EBITDA effect (`--amount`) always equals revenue effect minus cost effect; a different `--amount` is refused (E804). Giving `--amount` alone records a cost effect of minus the amount and a revenue effect of 0 (an add-back or one-off item lowers costs). A revenue-only EBITDA effect (a recast) is given as `--revenue-effect`.
- **Why:** The plan requires the equality. Most existing adjustments (director pay add-back, one-offs) are cost items, so `--amount` alone keeps working without Claude restating them.
- **How to change it:** `diligence/adjustments.py:resolve_effects`.

## 5. A reclassification between cost accounts has no effect; it names both accounts and the amount moved

- **Decision:** With `--from-account`, the category must be `reclassification`, both accounts must be expenses of the profit and loss within EBITDA (not finance cost, depreciation, interest expended or provisions, which EBITDA adds back), they must differ, `--moved-amount` is positive, and the revenue, cost and EBITDA effects are 0. It shows as a zero step in the bridge naming both accounts and the amount. A reclassification that moves revenue and cost together (revenue effect and cost effect of the same size) has no EBITDA effect and changes the margin on adjusted revenue.
- **Why:** The plan says it names both accounts and that a reclassification changes margins with zero EBITDA effect. fin has no cost-line margins (only EBITDA margin), so only revenue-and-cost moves change a margin it shows.
- **How to change it:** `adjustments.py:_check_reclassification`; to also show adjusted gross margin, extend `adjusted_ebitda` in `diligence_views.sql`.

## 6. Margins on adjusted revenue

- **Decision:** `adjusted_ebitda` gains `adjusted_revenue` (revenue plus accepted revenue effects) and the accepted revenue and cost effects. `adjusted_ebitda_margin` is adjusted EBITDA over adjusted revenue; `reported_ebitda_margin` stays on reported revenue. Only accepted adjustments count; proposed ones are still shown beside, not applied. The bridge text shows revenue, adjusted revenue and both margins only when accepted adjustments have a revenue effect.
- **Why:** Plan §4: margins on adjusted revenue. Showing both keeps the reported margin visible.
- **How to change it:** `diligence_views.sql`, views `adjusted_ebitda` and `ebitda_bridge`; `diligence/bridge.py:_bridge_block`.
