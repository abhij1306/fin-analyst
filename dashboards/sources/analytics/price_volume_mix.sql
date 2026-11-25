-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, prior_period, cast(price_effect as double) as price_effect, cast(volume_effect as double) as volume_effect, cast(mix_effect as double) as mix_effect, cast(new_revenue as double) as new_revenue, cast(discontinued_revenue as double) as discontinued_revenue, cast(change as double) as change
from price_volume_mix
union all
select null, null, null, null, null, null, null, null, null
where not exists (select 1 from price_volume_mix)
