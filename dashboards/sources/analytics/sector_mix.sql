-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, basis, segment, sector, customers, cast(revenue as double) as revenue, cast(share as double) as share
from sector_mix
union all
select null, null, null, null, null, null, null, null
where not exists (select 1 from sector_mix)
