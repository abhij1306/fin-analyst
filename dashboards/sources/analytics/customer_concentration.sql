-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, basis, segment, customers, cast(revenue as double) as revenue, cast(top1_share as double) as top1_share, cast(top5_share as double) as top5_share, cast(top10_share as double) as top10_share
from customer_concentration
union all
select null, null, null, null, null, null, null, null, null
where not exists (select 1 from customer_concentration)
