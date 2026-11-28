-- Company database: FDD F7, orderbook and revenue visibility (docs/fdd.md F7, docs/decisions/fdd-f7.md). Run after
-- diligence.sql (engagement, contracts, passthrough_customers), sources.sql (source_files, register_lines) and
-- diligence_split_views.sql (revenue_split). Money is rupees.

-- Orderbook (fin.diligence.orderbook): one row per customer order as the company's orderbook lists it as at as_at; file +
-- sheet + row lineage. customer is a token (the same as the registers'). value and billed are in the order's currency
-- as filed (currency 'INR' unless the file says otherwise; another currency counts in rupees only at a rate the user
-- stated, orderbook_valued); billed is NULL when the
-- cell was blank (counted as not billed, and flagged). order_date, start_date and end_date are NULL when blank.
CREATE TABLE orderbook_lines (
    file_id INTEGER NOT NULL,
    sheet VARCHAR NOT NULL,
    "row" INTEGER NOT NULL,
    as_at DATE NOT NULL,
    customer VARCHAR NOT NULL,
    order_no VARCHAR NOT NULL,
    order_date DATE,
    value DECIMAL(20, 2) NOT NULL,
    currency VARCHAR NOT NULL,
    start_date DATE,
    end_date DATE,
    billed DECIMAL(20, 2),
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (file_id, sheet, "row")
);

-- Exchange rates the user states for an engagement (`fin engagement rate <co> USD 84.10 --as-at 2025-03-31`,
-- fin.diligence.rates; SPEC-17 item 2): rupees per unit of the currency, as at a date. fin never fetches or assumes a
-- rate. Rows are appended; the latest stated for a currency and date is the one used (engagement_rates_current).
-- actor: who stated it (fin.actor: 'user', or 'ai:claude' when Claude ran the command for the user).
CREATE SEQUENCE engagement_rates_seq START 1;
CREATE TABLE engagement_rates (
    rate_id INTEGER PRIMARY KEY DEFAULT nextval('engagement_rates_seq'),
    engagement_id INTEGER NOT NULL,
    currency VARCHAR NOT NULL,
    rate DECIMAL(20, 6) NOT NULL CHECK (rate > 0),
    as_at DATE NOT NULL,
    actor VARCHAR NOT NULL CHECK (actor = 'user' OR actor LIKE 'ai:%'),
    run_id VARCHAR NOT NULL,
    stated_at TIMESTAMP NOT NULL
);

CREATE VIEW engagement_rates_current AS
SELECT * FROM engagement_rates
QUALIFY row_number() OVER (PARTITION BY engagement_id, currency, as_at ORDER BY rate_id DESC) = 1;

-- Each orderbook line with its value in rupees: an INR order as filed; an order in another currency converted at the rate
-- the user stated for its engagement, currency and the orderbook's as-at date (rate, rate_id), rounded to the paisa; with
-- no such rate value_inr and unbilled_inr are NULL ("not converted"). unbilled: value less billed to date, never below 0.
CREATE VIEW orderbook_valued AS
SELECT o.*, f.file_name, f.engagement_id, f.status AS file_status, r.rate, r.rate_id,
    CASE WHEN o.currency = 'INR' THEN o.value
         WHEN r.rate IS NOT NULL THEN CAST(round(o.value * r.rate, 2) AS DECIMAL(20, 2)) END AS value_inr,
    CASE WHEN o.currency = 'INR' THEN greatest(o.value - coalesce(o.billed, 0), 0)
         WHEN r.rate IS NOT NULL
         THEN CAST(round(greatest(o.value - coalesce(o.billed, 0), 0) * r.rate, 2) AS DECIMAL(20, 2)) END
        AS unbilled_inr,
    CASE WHEN o.currency = 'INR' THEN NULL
         WHEN r.rate IS NULL THEN 'order in ' || o.currency || ': not converted (no rate stated as at '
             || strftime(o.as_at, '%Y-%m-%d') || ')'
         ELSE o.currency || ' converted at ' || CAST(r.rate AS VARCHAR) || ' as at '
             || strftime(o.as_at, '%Y-%m-%d') || ' (rate stated by the user, #' || CAST(r.rate_id AS VARCHAR) || ')'
    END AS rate_note
FROM orderbook_lines o JOIN source_files f USING (file_id)
LEFT JOIN engagement_rates_current r
    ON r.engagement_id = f.engagement_id AND r.currency = o.currency AND r.as_at = o.as_at AND o.currency <> 'INR';

-- Days two inclusive date ranges share (0 when they do not meet).
CREATE MACRO overlap_days(s1, e1, s2, e2) AS
    greatest(0, date_diff('day', greatest(s1, s2), least(e1, e2)) + 1);

-- The latest loaded orderbook file of the latest engagement (latest as-at date, then latest file).
CREATE VIEW orderbook_current AS
SELECT o.* EXCLUDE (file_status)
FROM orderbook_valued o
WHERE o.file_status = 'loaded'
  AND o.file_id = (SELECT s.file_id FROM source_files s WHERE s.kind = 'orderbook' AND s.status = 'loaded'
      AND s.engagement_id = (SELECT max(engagement_id) FROM engagement)
      ORDER BY s.period_end DESC, s.file_id DESC LIMIT 1);

-- The year revenue visibility looks at: the latest engagement's last annual period, its end, and the 12 months after it
-- (year_start to year_end). period_end is the last day of the period under the engagement's fiscal start month.
CREATE VIEW visibility_basis AS
WITH p AS (
    SELECT engagement_id, string_split(periods, ',')[-1] AS period,
        make_date(2000 + CAST(substr(string_split(periods, ',')[-1], 3) AS INTEGER)
            - CASE WHEN fiscal_start_month > 1 THEN 1 ELSE 0 END, fiscal_start_month, 1) AS fy_start
    FROM engagement ORDER BY engagement_id DESC LIMIT 1
)
SELECT engagement_id, period,
    (fy_start + INTERVAL 1 YEAR - INTERVAL 1 DAY)::DATE AS period_end,
    (fy_start + INTERVAL 1 YEAR)::DATE AS year_start,
    (fy_start + INTERVAL 2 YEAR - INTERVAL 1 DAY)::DATE AS year_end
FROM p;

-- One row per item behind a backlog source, for the year after the last period (visibility_basis). remaining_amount is
-- what is still to be earned or billed after the as-at date of the source, covered_next_year the part of it that falls in
-- the year after the period end when it is spread straight-line over the item's remaining term (by days). NULL means it
-- could not be computed; note says why. Sources, never added together (an order can also be in a contract):
--   orderbook          unbilled orderbook value in rupees (orderbook_valued.unbilled_inr: an order in another currency
--                      only at a rate the user stated, else no amount) of the latest orderbook,
--                      spread from the later of its start date and the day after the as-at date to
--                      its end date; no end date: not spread; term over (it ended by the as-at date): 0, both
--                      still to come and in the next year (the unbilled value of an ended order is not backlog);
--   billed_not_earned  sales register lines (core customers) invoiced up to the period end whose service period runs
--                      past it: the amount less its share through the period end, and the share through the next year
--                      end less that (`day_share`, rounded once as the recast and the cut-off are, D6); lines without usable service dates are recognised on the invoice date and have none;
--   contract           customer contracts (F1, core customers) ending after the period end: contract value times the
--                      share of its days after the period end; a contract whose value was not read has no amount.
CREATE VIEW backlog_lines AS
WITH b AS (SELECT * FROM visibility_basis)
SELECT 'orderbook' AS source, o.file_name || ' ' || o.sheet || '!' || o."row" AS ref, o.customer AS counterparty,
    coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1)::DATE AS term_start, o.end_date AS term_end,
    CASE WHEN o.unbilled_inr IS NULL THEN CAST(NULL AS DECIMAL(20, 2))
         WHEN o.end_date < coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1) THEN 0
         ELSE o.unbilled_inr END AS remaining_amount,
    CASE WHEN o.unbilled_inr IS NULL THEN CAST(NULL AS DECIMAL(20, 2))
         WHEN o.end_date IS NULL THEN NULL
         WHEN o.end_date < coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1) THEN 0
         ELSE CAST(round(o.unbilled_inr
             * overlap_days(coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1), o.end_date, b.year_start, b.year_end)
             / (date_diff('day', coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1), o.end_date) + 1), 2)
             AS DECIMAL(20, 2)) END AS covered_next_year,
    CASE WHEN o.unbilled_inr IS NULL THEN o.rate_note
         ELSE nullif(concat_ws('; ', o.rate_note,
             CASE WHEN o.end_date IS NULL THEN 'no end date: not spread'
                  WHEN o.end_date < coalesce(greatest(o.start_date, o.as_at + 1), o.as_at + 1) THEN 'term over'
                  WHEN o.billed IS NULL THEN 'billed to date blank: counted as not billed'
                  WHEN o.billed > o.value THEN 'billed more than the order value' END), '') END AS note
FROM orderbook_current o, b
WHERE o.engagement_id = b.engagement_id
UNION ALL
SELECT 'billed_not_earned', f.file_name || ' ' || r.sheet || '!' || r."row", r.customer,
    greatest(r.service_start, b.period_end + 1)::DATE, r.service_end,
    r.amount_inr - day_share(r.amount_inr, greatest(date_diff('day', r.service_start, b.period_end) + 1, 0),
        date_diff('day', r.service_start, r.service_end) + 1),
    day_share(r.amount_inr, greatest(date_diff('day', r.service_start, least(r.service_end, b.year_end)) + 1, 0),
        date_diff('day', r.service_start, r.service_end) + 1)
        - day_share(r.amount_inr, greatest(date_diff('day', r.service_start, b.period_end) + 1, 0),
        date_diff('day', r.service_start, r.service_end) + 1),
    NULL
FROM register_lines r
JOIN source_files f ON f.file_id = r.file_id AND f.status = 'loaded' AND f.kind = 'registers'
JOIN b ON b.engagement_id = f.engagement_id
WHERE r.kind = 'sales' AND r.invoice_date <= b.period_end
  AND r.service_start IS NOT NULL AND r.service_end IS NOT NULL AND r.service_end >= r.service_start
  AND r.service_end > b.period_end
  AND r.customer NOT IN (SELECT p.customer FROM passthrough_customers p WHERE p.engagement_id = b.engagement_id)
UNION ALL
SELECT 'contract', c.file_name || ' p.' || c.page, c.counterparty,
    greatest(c.start_date, b.period_end + 1)::DATE, c.end_date,
    CAST(round(c.value * (date_diff('day', greatest(c.start_date, b.period_end + 1), c.end_date) + 1)
        / (date_diff('day', c.start_date, c.end_date) + 1), 2) AS DECIMAL(20, 2)),
    CAST(round(c.value * overlap_days(c.start_date, c.end_date, b.year_start, b.year_end)
        / (date_diff('day', c.start_date, c.end_date) + 1), 2) AS DECIMAL(20, 2)),
    CASE WHEN c.value IS NULL THEN 'value not read' END
FROM contracts c JOIN b ON b.engagement_id = c.engagement_id
WHERE c.party_kind = 'customer' AND c.end_date > b.period_end
  AND c.counterparty NOT IN (SELECT p.customer FROM passthrough_customers p WHERE p.engagement_id = b.engagement_id);

-- Revenue visibility: per source, the items, the backlog still to come (remaining_amount) and the part covered in the year
-- after the last period (covered_next_year), against the last period's core revenue (revenue_split). Every source has a
-- row; items = 0 means it is not loaded. unusable_items: items with no amount or not spread (see backlog_lines.note);
-- covered_pct = covered_next_year / core_revenue (NULL when core revenue is not positive). The sources are not added.
CREATE VIEW revenue_visibility AS
WITH b AS (SELECT * FROM visibility_basis),
agg AS (
    SELECT source, count(*) AS items, sum(remaining_amount) AS remaining_amount,
        sum(covered_next_year) AS covered_next_year,
        count(*) FILTER (WHERE remaining_amount IS NULL OR covered_next_year IS NULL) AS unusable_items
    FROM backlog_lines GROUP BY source
)
SELECT b.engagement_id, b.period, b.period_end, b.year_start, b.year_end, s.source,
    coalesce(a.items, 0) AS items, a.remaining_amount, a.covered_next_year,
    coalesce(a.unusable_items, 0) AS unusable_items,
    rs.core_revenue,
    CASE WHEN rs.core_revenue > 0 THEN CAST(round(100 * a.covered_next_year / rs.core_revenue, 2) AS DECIMAL(20, 2)) END
        AS covered_pct
FROM b
CROSS JOIN (VALUES ('orderbook', 1), ('billed_not_earned', 2), ('contract', 3)) AS s(source, ord)
LEFT JOIN agg a ON a.source = s.source
LEFT JOIN revenue_split rs ON rs.engagement_id = b.engagement_id AND rs.period = b.period
ORDER BY s.ord;
