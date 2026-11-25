-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, scope, cast(ebitda as double) as ebitda, cast(adjustments as double) as adjustments, cast(adjusted_ebitda as double) as adjusted_ebitda, cast(non_cash_and_other as double) as non_cash_and_other, cast(working_capital_change as double) as working_capital_change, cast(taxes_paid as double) as taxes_paid, cast(other_operating as double) as other_operating, cast(cfo as double) as cfo, cast(capex as double) as capex, cast(free_cash_flow as double) as free_cash_flow, cast(cash_conversion as double) as cash_conversion
from qoe_walk
union all
select null, null, null, null, null, null, null, null, null, null, null, null, null, null
where not exists (select 1 from qoe_walk)
