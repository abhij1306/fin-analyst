# Decisions: d9-analytics

Product-level choices for customer, product, sector, pricing and retention analytics (DILIGENCE_PLAN.md §7 D9), written 2026-11-25 by the builder. Each is a builder's reading until the owner confirms or changes it.

## 1. Core and pass-through are separate; retention, cohorts and price/volume/mix are core only

- **Decision:** Every customer view has a segment: `core` (customers not on the pass-through list) or `passthrough`. Concentration and sector mix show both segments, each as a share of its own segment's revenue. Retention, cohorts and price/volume/mix use core revenue only.
- **Why:** The plan says core and pass-through are shown separately, and pass-through (SI/reseller deals at about zero margin) would distort retention and pricing. Concentration is shown per segment so a pass-through reseller does not hide the concentration of core customers.
- **How to change it:** `db/schema/diligence_analytics.sql`: `analytics_sales.passthrough`, `customer_core_years`, the `WHERE NOT passthrough` in `product_pvm`.

## 2. The stub is shown in concentration and mix, never in retention, cohorts or price/volume/mix

- **Decision:** The current-year stub (unaudited, a part year) appears in `customer_revenue`, `customer_concentration`, `sector_mix` and `product_revenue` with basis `unaudited`. It is not compared with a full year anywhere.
- **Why:** Plan §2 says the stub is never annualised; comparing a part year with a full year would show false churn and a false fall in volume.
- **How to change it:** `diligence_analytics.sql`: the `basis = 'audited'` filters in `customer_core_years`, `customer_retention` and `product_pvm`.

## 3. A customer is active in a year when its core revenue is positive; retention is year on year

- **Decision:** Retention compares each annual period with the previous one (`prior_fy`), and only when both are in the engagement. A customer is active with positive net core revenue in the year (a net credit is not retained). Net retention = this year's revenue from last year's active customers / their revenue last year (new customers excluded, expansion included). Gross retention = (last year's revenue less lost customers' revenue less the fall at customers that spend less) / last year's revenue (no credit for spending more). Logo retention counts customers. A customer that skips a year and returns is lost, then new again.
- **Why:** The plan asks for net revenue retention by year. The usual definitions are used and the parts (lost, contraction, expansion, new) are shown so any other reading can be made from the same view.
- **How to change it:** `diligence_analytics.sql`: `customer_retention`, `customer_core_years` (the `HAVING sum > 0`).

## 4. Cohorts are by the first year with revenue; the first period is the opening book

- **Decision:** A customer's cohort is the first annual period in the engagement in which it has positive core revenue. Cohort retention is against the cohort's first period (customers active again, and revenue); a customer that skips a year and returns counts as active when it returns. The cohort of the first period in the engagement is flagged `opening`: those customers may be older, so it is not a cohort won that year.
- **Why:** The registers only cover the engagement, so the true first year of an old customer is not known.
- **How to change it:** `diligence_analytics.sql`: `customer_cohorts`.

## 5. Price, volume and mix are on priced lines only, with a volume index at last year's prices

- **Decision:** Only core sales lines with a quantity above zero and a rate are priced. Per product and annual period, price = revenue (Amount in Rs) / quantity, in rupees per unit (a foreign-currency line uses its rupee amount). For a product priced in both years: price effect = this year's revenue less this year's quantity at last year's price; volume effect = last year's revenue times (V - 1), where V = sum of this year's quantity at last year's prices over the continuing products / their last year's revenue; mix effect = the rest of the change. So the three add up to the change in the product's revenue, and the mix effects add up to zero. A product priced only this year is `new` and one priced only last year is `discontinued`: their revenue is shown, with no effects. Lines with no quantity or rate are in `product_revenue` but not in price, volume and mix.
- **Why:** Products have different units, so quantities cannot be added; an index at last year's prices avoids that. The plan does not say how mix is defined.
- **How to change it:** `diligence_analytics.sql`: `product_pvm` (`effects` and `indexed`).

## 6. A sector is the user's, and a customer without one is shown as unmapped

- **Decision:** Claude proposes a sector per customer token from what it bought (it never sees a name); the user confirms (or gives another sector) or rejects. Only confirmed sectors count; the rest of the revenue is a row `unmapped` in the sector mix with its customer count. Sectors are free text (trimmed, kept as written); the word `unmapped` is refused as a sector. A confirmed sector is changed only by the user.
- **Why:** Plan §7 D9: Claude fills, the user reviews. An unreviewed sector must not move the mix.
- **How to change it:** `diligence/customers.py` (`propose`, `confirm`, `check_sector`), `diligence_analytics.sql`: `sector_mix`, `analytics_sales` (the join on `status = 'confirmed'`).

## 7. Concentration is of the segment's revenue and counts every customer

- **Decision:** Top 1, 5 and 10 are the largest customers by revenue in the period and segment (a tie goes to the lower token); their share is of that segment's revenue in the period, and with fewer than 5 or 10 customers it is all of them (100%). A customer with a net negative revenue is ranked last and counts in the total.
- **How to change it:** `diligence_analytics.sql`: `customer_revenue` (`rank`), `customer_concentration`.

## 8. The report section describes only

- **Decision:** The health report's "Customers, sectors and pricing" section (and the dashboard page) show the tables with a plain description; no narrative placeholder, no judgement. It appears only for a company with an engagement and sales register lines.
- **How to change it:** `report/customers.py`.
