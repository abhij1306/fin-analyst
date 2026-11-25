-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, prior_period, prior_customers, retained_customers, lost_customers, new_customers, cast(prior_revenue as double) as prior_revenue, cast(retained_revenue as double) as retained_revenue, cast(lost_revenue as double) as lost_revenue, cast(new_revenue as double) as new_revenue, cast(net_retention as double) as net_retention, cast(gross_retention as double) as gross_retention, cast(logo_retention as double) as logo_retention
from customer_retention
union all
select null, null, null, null, null, null, null, null, null, null, null, null, null, null
where not exists (select 1 from customer_retention)
