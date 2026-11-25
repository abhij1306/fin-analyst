-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, basis, cast(core_revenue as double) as core_revenue, cast(passthrough_revenue as double) as passthrough_revenue, cast(passthrough_share as double) as passthrough_share, cast(core_revenue_growth as double) as core_revenue_growth
from qoe_revenue
union all
select null, null, null, null, null, null, null
where not exists (select 1 from qoe_revenue)
