-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, scope, cast(revenue as double) as revenue, cast(adjusted_revenue as double) as adjusted_revenue, cast(reported_ebitda as double) as reported_ebitda, cast(accepted_adjustments as double) as accepted_adjustments, cast(adjusted_ebitda as double) as adjusted_ebitda, cast(reported_ebitda_margin as double) as reported_ebitda_margin, cast(adjusted_ebitda_margin as double) as adjusted_ebitda_margin, cast(proposed_adjustments as double) as proposed_adjustments
from qoe_ebitda
union all
select null, null, null, null, null, null, null, null, null, null, null
where not exists (select 1 from qoe_ebitda)
