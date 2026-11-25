-- Company database: customer, product, sector, pricing and retention analytics on the registers
-- (docs/DILIGENCE_PLAN.md D9). Run after diligence_split_views.sql (revenue_split) and sources.sql (register_lines).
--
-- Which lines: the sales register lines of the latest engagement's loaded (not superseded) files, by
-- register_lines.period (a line dated outside the engagement has no period and is left out). Amounts are Amount in Rs,
-- before GST, as filed. A customer is a token. Every view splits core from pass-through (segment): the customers on
-- the latest engagement's passthrough_customers are pass-through, and pass-through revenue never enters retention,
-- cohorts or price/volume/mix. The current-year stub (basis 'unaudited') is shown in concentration and mix and is
-- never part of retention, cohorts or price/volume/mix (it is a part year, never annualised).
-- Money is exact DECIMAL(38, 2) rupees; shares are div6 fractions (0.426761 means 42.6761%).

-- The sector of each customer (fin.diligence.customers): one row per customer token of the loaded sales registers.
--   pending    not proposed yet;
--   proposed   Claude proposed a sector (`fin map customers <co> <file>`): sector, confidence, reason;
--   confirmed  the user confirmed it (`fin review confirm <co> <ids> --customers`); only confirmed sectors count;
--   rejected   the user rejected the proposal.
-- A customer without a confirmed sector is 'unmapped' in sector_mix, never guessed.
CREATE SEQUENCE customers_seq START 1;
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY DEFAULT nextval('customers_seq'),
    customer VARCHAR NOT NULL UNIQUE,
    status VARCHAR NOT NULL CHECK (status IN ('pending', 'proposed', 'confirmed', 'rejected')),
    sector VARCHAR,
    confidence DOUBLE,
    reason VARCHAR,
    run_id VARCHAR NOT NULL,  -- the run of the last change
    changed_at TIMESTAMP NOT NULL
);

-- The base of every view below: one row per sales register line of the latest engagement. period_no is the position of
-- the period in the engagement's list; sector is the customer's confirmed sector (NULL when none).
CREATE VIEW analytics_sales AS
WITH latest AS (
    SELECT engagement_id, periods, stub FROM engagement ORDER BY engagement_id DESC LIMIT 1
)
SELECT
    l.engagement_id, r.period,
    CASE WHEN r.period = l.stub THEN 'unaudited' ELSE 'audited' END AS basis,
    list_position(string_split(l.periods, ','), r.period) AS period_no,
    r.customer,
    r.customer IN (SELECT p.customer FROM passthrough_customers p WHERE p.engagement_id = l.engagement_id)
        AS passthrough,
    c.sector, r.product, r.quantity, r.rate, r.amount_inr
FROM register_lines r
JOIN source_files f ON f.file_id = r.file_id AND f.status = 'loaded'
JOIN latest l ON l.engagement_id = f.engagement_id
LEFT JOIN customers c ON c.customer = r.customer AND c.status = 'confirmed'
WHERE r.kind = 'sales' AND r.period IS NOT NULL;

-- Revenue per period, segment ('core' or 'passthrough') and customer, with its share of the segment's revenue in the
-- period and its rank in it (1 = largest; a tie goes to the lower token).
CREATE VIEW customer_revenue AS
WITH per AS (
    SELECT
        engagement_id, period, basis, period_no,
        CASE WHEN passthrough THEN 'passthrough' ELSE 'core' END AS segment,
        customer, any_value(sector) AS sector,
        CAST(sum(amount_inr) AS DECIMAL(38, 2)) AS revenue, count(*) AS lines
    FROM analytics_sales
    GROUP BY engagement_id, period, basis, period_no, passthrough, customer
)
SELECT
    p.engagement_id, p.period, p.basis, p.segment, p.customer, p.sector, p.revenue, p.lines,
    div6(to_paise(p.revenue), to_paise(sum(p.revenue) OVER (PARTITION BY p.period, p.segment))) AS share,
    CAST(row_number() OVER (PARTITION BY p.period, p.segment ORDER BY p.revenue DESC, p.customer) AS INTEGER) AS rank
FROM per p
ORDER BY p.period_no, p.segment, rank;

-- Customer concentration per period and segment: the customers, the revenue, and the revenue and share of the top 1,
-- 5 and 10 customers (all of them when there are fewer). Share is of the segment's revenue in the period.
CREATE VIEW customer_concentration AS
SELECT
    engagement_id, period, basis, segment,
    CAST(count(*) AS INTEGER) AS customers,
    CAST(sum(revenue) AS DECIMAL(38, 2)) AS revenue,
    CAST(sum(revenue) FILTER (WHERE rank <= 1) AS DECIMAL(38, 2)) AS top1_revenue,
    CAST(sum(revenue) FILTER (WHERE rank <= 5) AS DECIMAL(38, 2)) AS top5_revenue,
    CAST(sum(revenue) FILTER (WHERE rank <= 10) AS DECIMAL(38, 2)) AS top10_revenue,
    div6(to_paise(sum(revenue) FILTER (WHERE rank <= 1)), to_paise(sum(revenue))) AS top1_share,
    div6(to_paise(sum(revenue) FILTER (WHERE rank <= 5)), to_paise(sum(revenue))) AS top5_share,
    div6(to_paise(sum(revenue) FILTER (WHERE rank <= 10)), to_paise(sum(revenue))) AS top10_share
FROM customer_revenue
GROUP BY engagement_id, period, basis, segment
ORDER BY period, segment;

-- Core revenue per customer and annual period, for retention and cohorts: only customers with positive revenue are
-- active in a period (a net credit is not a customer retained).
CREATE VIEW customer_core_years AS
SELECT s.engagement_id, s.period, s.period_no, s.customer, CAST(sum(s.amount_inr) AS DECIMAL(38, 2)) AS revenue
FROM analytics_sales s
WHERE NOT s.passthrough AND s.basis = 'audited'
GROUP BY s.engagement_id, s.period, s.period_no, s.customer
HAVING sum(s.amount_inr) > 0;

-- Net and gross revenue retention of core revenue, one row per annual period whose previous annual period (prior_fy)
-- is also in the engagement (so none for the first period or the stub). Against the previous period's customers:
--   prior_customers / prior_revenue: customers (and their revenue) active in the previous period;
--   retained_customers / retained_revenue: of those, active again now, and their revenue now;
--   lost_customers / lost_revenue: of those, not active now, and their previous revenue;
--   expansion / contraction: the rise and the fall (shown positive) in revenue of the retained customers;
--   new_customers / new_revenue: active now and not in the previous period;
--   net_retention = retained_revenue / prior_revenue; gross_retention = (prior_revenue - lost_revenue - contraction)
--   / prior_revenue; logo_retention = retained_customers / prior_customers.
-- retained_revenue = prior_revenue - lost_revenue - contraction + expansion.
CREATE VIEW customer_retention AS
WITH periods AS (
    SELECT DISTINCT engagement_id, period FROM analytics_sales WHERE basis = 'audited'
),
pairs AS (
    SELECT p.engagement_id, p.period, prior_fy(p.period) AS prior_period
    FROM periods p
    WHERE prior_fy(p.period) IN (SELECT period FROM periods)
),
joined AS (
    SELECT
        k.engagement_id, k.period, k.prior_period, k.customer,
        coalesce(cur.revenue, 0) AS cur, coalesce(prev.revenue, 0) AS prev
    FROM (
        SELECT DISTINCT p.engagement_id, p.period, p.prior_period, y.customer
        FROM pairs p JOIN customer_core_years y ON y.period IN (p.period, p.prior_period)
    ) k
    LEFT JOIN customer_core_years cur ON cur.period = k.period AND cur.customer = k.customer
    LEFT JOIN customer_core_years prev ON prev.period = k.prior_period AND prev.customer = k.customer
),
figures AS (
    SELECT
        engagement_id, period, prior_period,
        CAST(count(*) FILTER (WHERE prev > 0) AS INTEGER) AS prior_customers,
        CAST(coalesce(sum(prev) FILTER (WHERE prev > 0), 0) AS DECIMAL(38, 2)) AS prior_revenue,
        CAST(count(*) FILTER (WHERE prev > 0 AND cur > 0) AS INTEGER) AS retained_customers,
        CAST(coalesce(sum(cur) FILTER (WHERE prev > 0 AND cur > 0), 0) AS DECIMAL(38, 2)) AS retained_revenue,
        CAST(count(*) FILTER (WHERE prev > 0 AND cur = 0) AS INTEGER) AS lost_customers,
        CAST(coalesce(sum(prev) FILTER (WHERE prev > 0 AND cur = 0), 0) AS DECIMAL(38, 2)) AS lost_revenue,
        CAST(coalesce(sum(cur - prev) FILTER (WHERE prev > 0 AND cur > prev), 0) AS DECIMAL(38, 2)) AS expansion,
        CAST(coalesce(sum(prev - cur) FILTER (WHERE prev > 0 AND cur > 0 AND cur < prev), 0) AS DECIMAL(38, 2))
            AS contraction,
        CAST(count(*) FILTER (WHERE prev = 0 AND cur > 0) AS INTEGER) AS new_customers,
        CAST(coalesce(sum(cur) FILTER (WHERE prev = 0 AND cur > 0), 0) AS DECIMAL(38, 2)) AS new_revenue
    FROM joined
    GROUP BY engagement_id, period, prior_period
)
SELECT
    f.*,
    div6(to_paise(f.retained_revenue), to_paise(f.prior_revenue)) AS net_retention,
    div6(to_paise(f.prior_revenue - f.lost_revenue - f.contraction), to_paise(f.prior_revenue)) AS gross_retention,
    div6(CAST(f.retained_customers AS HUGEINT), CAST(f.prior_customers AS HUGEINT)) AS logo_retention
FROM figures f
ORDER BY f.period;

-- Cohorts of core customers: a customer's cohort is the first annual period in which it is active. Each row is a
-- cohort and an annual period of the engagement from its start (a cohort with nobody left still has its rows, with
-- zeros): customers active again (a customer may skip a period and return), their
-- revenue, and retention against the cohort's first period (logo = active / cohort customers, revenue = revenue /
-- cohort revenue). opening is true for the first period of the engagement: those customers may be older, so it is
-- the opening book, not a cohort won in that year.
CREATE VIEW customer_cohorts AS
WITH first AS (
    SELECT customer, min(period_no) AS cohort_no FROM customer_core_years GROUP BY customer
),
periods AS (
    SELECT DISTINCT engagement_id, period, period_no FROM analytics_sales WHERE basis = 'audited'
),
grid AS (
    SELECT p.engagement_id, c.cohort_no, p.period, p.period_no
    FROM (SELECT DISTINCT cohort_no FROM first) c JOIN periods p ON p.period_no >= c.cohort_no
),
activity AS (
    SELECT f.cohort_no, y.period_no, count(*) AS active_customers, sum(y.revenue) AS revenue
    FROM customer_core_years y JOIN first f ON f.customer = y.customer AND y.period_no >= f.cohort_no
    GROUP BY f.cohort_no, y.period_no
),
sized AS (
    SELECT
        g.engagement_id, g.cohort_no, g.period, g.period_no,
        CAST(coalesce(a.active_customers, 0) AS INTEGER) AS active_customers,
        CAST(coalesce(a.revenue, 0) AS DECIMAL(38, 2)) AS revenue
    FROM grid g LEFT JOIN activity a ON a.cohort_no = g.cohort_no AND a.period_no = g.period_no
)
SELECT
    s.engagement_id,
    (SELECT p.period FROM periods p WHERE p.period_no = s.cohort_no) AS cohort,
    s.cohort_no = (SELECT min(period_no) FROM customer_core_years) AS opening,
    s.period,
    s.active_customers,
    s.revenue,
    z.active_customers AS cohort_customers,
    z.revenue AS cohort_revenue,
    div6(CAST(s.active_customers AS HUGEINT), CAST(z.active_customers AS HUGEINT)) AS logo_retention,
    div6(to_paise(s.revenue), to_paise(z.revenue)) AS revenue_retention
FROM sized s
JOIN sized z ON z.cohort_no = s.cohort_no AND z.period_no = s.cohort_no
ORDER BY s.cohort_no, s.period_no;

-- Revenue per period, segment and product (as filed; a line with no product is 'unspecified'), with the lines that
-- give a quantity and a rate (priced_lines) and their quantity and revenue (priced_quantity, priced_revenue).
CREATE VIEW product_revenue AS
SELECT
    engagement_id, period, basis,
    CASE WHEN passthrough THEN 'passthrough' ELSE 'core' END AS segment,
    coalesce(product, 'unspecified') AS product,
    CAST(sum(amount_inr) AS DECIMAL(38, 2)) AS revenue,
    CAST(count(*) AS INTEGER) AS lines,
    CAST(count(*) FILTER (WHERE quantity > 0 AND rate IS NOT NULL) AS INTEGER) AS priced_lines,
    CAST(coalesce(sum(quantity) FILTER (WHERE quantity > 0 AND rate IS NOT NULL), 0) AS DECIMAL(38, 6))
        AS priced_quantity,
    CAST(coalesce(sum(amount_inr) FILTER (WHERE quantity > 0 AND rate IS NOT NULL), 0) AS DECIMAL(38, 2))
        AS priced_revenue
FROM analytics_sales
GROUP BY engagement_id, period, basis, period_no, passthrough, coalesce(product, 'unspecified')
ORDER BY period_no, segment, revenue DESC, product;

-- Price, volume and mix of core products, one row per annual period (whose previous annual period is also in the
-- engagement) and product that has lines with a quantity and a rate (priced lines only). price = revenue / quantity
-- in rupees per unit. kind:
--   continuing    priced in both periods:
--       price_effect  = revenue - quantity * prior price (what the change in price did to this period's revenue);
--       volume_effect = prior revenue * (V - 1), V = sum(quantity * prior price) / sum(prior revenue) over the
--                       continuing products of the period (a volume index at the prior prices, so products in
--                       different units add up);
--       mix_effect    = the rest of the change in revenue (the products' shares of volume moved); it adds up to 0
--                       across the continuing products;
--       price_effect + volume_effect + mix_effect = revenue - prior_revenue;
--   new           priced only in this period: its revenue is new_revenue, no effects;
--   discontinued  priced only in the previous period: its revenue is discontinued_revenue.
CREATE VIEW product_pvm AS
WITH prod AS (
    SELECT
        engagement_id, period, product,
        sum(quantity) AS quantity, sum(amount_inr) AS revenue
    FROM analytics_sales
    WHERE NOT passthrough AND basis = 'audited' AND quantity > 0 AND rate IS NOT NULL AND product IS NOT NULL
    GROUP BY engagement_id, period, product
    HAVING sum(quantity) > 0
),
periods AS (
    SELECT DISTINCT engagement_id, period FROM analytics_sales WHERE basis = 'audited'
),
pairs AS (
    SELECT engagement_id, period, prior_fy(period) AS prior_period FROM periods
    WHERE prior_fy(period) IN (SELECT period FROM periods)
),
joined AS (
    SELECT
        p.engagement_id, p.period, p.prior_period, coalesce(c.product, e.product) AS product,
        e.quantity AS prior_quantity, e.revenue AS prior_revenue, c.quantity, c.revenue,
        CASE WHEN e.product IS NULL THEN 'new' WHEN c.product IS NULL THEN 'discontinued' ELSE 'continuing' END
            AS kind
    FROM (
        SELECT DISTINCT p.engagement_id, p.period, p.prior_period, k.product
        FROM pairs p JOIN prod k ON k.period IN (p.period, p.prior_period)
    ) p
    LEFT JOIN prod c ON c.period = p.period AND c.product = p.product
    LEFT JOIN prod e ON e.period = p.prior_period AND e.product = p.product
),
indexed AS (
    SELECT
        j.*,
        CASE WHEN kind = 'continuing' THEN CAST(prior_revenue AS DOUBLE) * CAST(quantity AS DOUBLE)
            / CAST(prior_quantity AS DOUBLE) END AS at_prior_price,
        sum(CASE WHEN kind = 'continuing' THEN CAST(prior_revenue AS DOUBLE) * CAST(quantity AS DOUBLE)
            / CAST(prior_quantity AS DOUBLE) END) OVER (PARTITION BY period) AS index_numerator,
        sum(CASE WHEN kind = 'continuing' THEN CAST(prior_revenue AS DOUBLE) END) OVER (PARTITION BY period)
            AS index_denominator
    FROM joined j
),
effects AS (
    SELECT
        i.*,
        CAST(round(revenue - at_prior_price, 2) AS DECIMAL(38, 2)) AS price_effect,
        CASE WHEN kind = 'continuing' AND index_denominator > 0 THEN CAST(round(
            CAST(prior_revenue AS DOUBLE) * (index_numerator / index_denominator - 1), 2) AS DECIMAL(38, 2)) END
            AS volume_effect
    FROM indexed i
)
SELECT
    engagement_id, period, prior_period, product, kind,
    CAST(prior_quantity AS DECIMAL(38, 6)) AS prior_quantity,
    CAST(quantity AS DECIMAL(38, 6)) AS quantity,
    CAST(prior_revenue AS DECIMAL(38, 2)) AS prior_revenue,
    CAST(revenue AS DECIMAL(38, 2)) AS revenue,
    CAST(round(CAST(prior_revenue AS DOUBLE) / CAST(prior_quantity AS DOUBLE), 2) AS DECIMAL(38, 2)) AS prior_price,
    CAST(round(CAST(revenue AS DOUBLE) / CAST(quantity AS DOUBLE), 2) AS DECIMAL(38, 2)) AS price,
    price_effect, volume_effect,
    CASE WHEN kind = 'continuing' THEN CAST(revenue - prior_revenue - price_effect - volume_effect AS DECIMAL(38, 2))
        END AS mix_effect,
    CAST(CASE WHEN kind = 'new' THEN revenue END AS DECIMAL(38, 2)) AS new_revenue,
    CAST(CASE WHEN kind = 'discontinued' THEN prior_revenue END AS DECIMAL(38, 2)) AS discontinued_revenue
FROM effects
ORDER BY period, kind, product;

-- product_pvm totalled per period: the price, volume and mix effects of the continuing products, the revenue of
-- the new and discontinued ones, and the change in priced revenue they explain
-- (change = price + volume + mix + new - discontinued).
CREATE VIEW product_pvm_summary AS
SELECT
    engagement_id, period, prior_period,
    CAST(count(*) FILTER (WHERE kind = 'continuing') AS INTEGER) AS continuing_products,
    CAST(coalesce(sum(price_effect), 0) AS DECIMAL(38, 2)) AS price_effect,
    CAST(coalesce(sum(volume_effect), 0) AS DECIMAL(38, 2)) AS volume_effect,
    CAST(coalesce(sum(mix_effect), 0) AS DECIMAL(38, 2)) AS mix_effect,
    CAST(coalesce(sum(new_revenue), 0) AS DECIMAL(38, 2)) AS new_revenue,
    CAST(coalesce(sum(discontinued_revenue), 0) AS DECIMAL(38, 2)) AS discontinued_revenue,
    CAST(coalesce(sum(revenue), 0) - coalesce(sum(prior_revenue), 0) AS DECIMAL(38, 2)) AS change
FROM product_pvm
GROUP BY engagement_id, period, prior_period
ORDER BY period;

-- Revenue by sector per period and segment. A customer's sector is its confirmed one (the customers table); the
-- others are 'unmapped' (never guessed): customers counts them, so the size of the gap is always shown.
CREATE VIEW sector_mix AS
SELECT
    engagement_id, period, basis, segment,
    coalesce(sector, 'unmapped') AS sector,
    CAST(count(*) AS INTEGER) AS customers,
    CAST(sum(revenue) AS DECIMAL(38, 2)) AS revenue,
    div6(to_paise(sum(revenue)), to_paise(sum(sum(revenue)) OVER (PARTITION BY period, segment))) AS share
FROM customer_revenue
GROUP BY engagement_id, period, basis, segment, coalesce(sector, 'unmapped')
ORDER BY period, segment, revenue DESC, sector;
