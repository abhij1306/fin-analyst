# F7: orderbook and revenue visibility

Builder readings of `docs/fdd.md` F7; the owner confirms or changes them. Format: [README.md](README.md).

## 1. The orderbook is a snapshot with one as-at date, in rupees only

- **Decision**: the orderbook has a title line "Orderbook as at <date>"; the date must fall in an annual period of the open
  engagement or its stub (E808 otherwise), as for a payroll snapshot, and gives the file's period. A later file for the same
  period replaces the earlier. "Latest orderbook" is the loaded file with the latest as-at date. Value and billed to date are kept in the
  order's currency; only INR orders have a rupee amount. An order in another currency is loaded and shown ("not converted") but
  has no backlog amount and never reaches a threshold. A blank billed-to-date cell is stored as NULL, counted as not billed and
  listed.
- **Why**: the plan gives a currency column but no rate, and the registers' rupee amounts come from the company's own rate; inventing
  a rate would be a silent assumption. A blank billed cell and "not billed" differ, so it is flagged rather than turned into 0.
- **How to change it**: `db/schema/fdd_orderbook.sql` (`backlog_lines`, orderbook branch), `diligence/orderbook.py`.
  [Client question 10](../client-questions.md) (currencies). The real export's headers: the client's agent writes a profile when E807 shows them
  (agent reading, 2026-11-26; `templates/profiles/orderbook.toml` has synthetic ones).

## 2. How each backlog source is computed

- **Decision**: three sources, never added together because an order can also be in a contract.
  - Unbilled orderbook: value less billed to date, never below 0 (an order billed above its value is flagged), spread straight-line by days
    from the later of its start date and the day after the as-at date to its end date. No end date: counted in the backlog, not spread.
    End date before that start: 0 still to come and 0 covered ("term over"; changed by [fdd-review-fixes.md](fdd-review-fixes.md)).
  - Billed, not yet earned: core sales register lines invoiced up to the last period end whose service period runs past it: the amount
    times the share of service days after the period end. It is the service-period recast (D6) share, computed by day in SQL; it can differ from
    the recast's monthly paise rounding by under a rupee. A line with no usable service dates is recognised on its invoice date and has none.
  - Contract value remaining: customer contracts (F1) ending after the period end, core customers: value times the share of its days after the
    period end (straight-line). A contract whose value was not read has no amount ("value not read").
- **Why**: the plan says "spread straight-line over the remaining term". Pass-through customers are left out of the register and contract sources
  because the comparison is with core revenue.
- **How to change it**: `db/schema/fdd_orderbook.sql` (`backlog_lines`).

## 3. Revenue visibility year and the comparison

- **Decision**: the visibility year is the 12 months after the last annual period's end (not after the stub or the as-at date). For each source:
  the backlog still to come, the part falling in that year, and that part as a percentage of the last annual period's core revenue
  (`revenue_split.core_revenue`; NULL when it is not positive). The orderbook's part uses its own remaining term, so an as-at date after the
  period end only shortens what is left to spread.
- **Why**: "the year after the last period end, against the last year's core revenue" (plan). A stub is a part year, so it is not the base.
- **How to change it**: `visibility_basis` and `revenue_visibility` in `db/schema/fdd_orderbook.sql`.

## 4. Orders with no billing, billing with no order

- **Decision**: reconciliation `orderbook_vs_register` runs per period when an orderbook and sales register lines exist for it. The register has
  no order reference, so orders and billing are matched by customer token. An order whose customer has no sales register line in the period is
  `left_only` (left = order value). A core customer with register billing in the period and no order on the orderbook is `right_only` (right =
  billing). Lines at or above the engagement threshold need a query (new detail level `order`); below it they are listed. A customer in both is
  not listed: no amounts are compared. Gate G2 requires the run when its inputs are loaded.
- **Why**: the plan asks only for the two existence tests; a billed-versus-invoiced amount comparison would need an order reference the register
  lacks. Matching by customer means a customer with one billed order hides another order with no billing; the note says what the orderbook's
  billed to date is so the user can see it.
- **How to change it**: `reconcile/orderbook_vs_register.py`. [Client question 10](../client-questions.md) (open orders only or all orders; an order number in the register).
